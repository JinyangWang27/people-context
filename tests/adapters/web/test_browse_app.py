"""The `pctx browse` application: its guard, security headers, read endpoints (M30.1), and batch review (M30.2)."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from people_context import cli
from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, SqliteRecordStore, open_db
from people_context.adapters.web import TOKEN_HEADER, create_browse_app
from people_context.adapters.web import app as web_app
from people_context.app.exports.brief import BRIEF_CONTEXT_ITEMS
from people_context.app.imports import ImportPipelineError, ImportReviewRow
from people_context.app.records import RecordFact, RecordFactInput
from people_context.cli.imports import REVIEW_DISCLOSURE_WARNING
from people_context.cli.rendering import import_review_lines
from people_context.cli.sources import SOURCES_DISCLOSURE_WARNING
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity

_PORT = 8123
_ORIGIN = f"http://127.0.0.1:{_PORT}"
_TOKEN = "launch-token-for-elena"
_MARKUP_NAME = '<img src=x onerror="alert(1)">Toma'
_SENSITIVE_VALUE = "Private recovery detail"


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _seed_people(db_file: Path) -> dict[str, str]:
    """Elena with an ordinary and a sensitive fact, Toma (markup name) over the brief's bound."""
    conn = open_db(db_file)
    try:
        repository = SqlitePeopleRepository(conn)
        record_fact = RecordFact(repository, SqliteRecordStore(conn), SqliteAuditLog(conn), _Clock())
        elena = Person(canonical_name="Elena Marsh", summary="Plans the allotment")
        toma = Person(canonical_name=_MARKUP_NAME)
        repository.save_person(elena)
        repository.save_person(toma)
        record_fact.execute(RecordFactInput(person_id=elena.id, predicate="role", value="Gardener", source="cli"))
        record_fact.execute(
            RecordFactInput(
                person_id=elena.id,
                predicate="health",
                value=_SENSITIVE_VALUE,
                sensitivity=Sensitivity.SENSITIVE,
                source="cli",
            )
        )
        for index in range(BRIEF_CONTEXT_ITEMS + 1):
            record_fact.execute(
                RecordFactInput(person_id=toma.id, predicate=f"note_{index:02d}", value=f"v{index}", source="cli")
            )
        return {"elena": elena.id, "toma": toma.id}
    finally:
        conn.close()


def _cli_json(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    assert cli.main(list(argv)) == 0
    document = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def _seed_committed_source(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *receipt: str
) -> str:
    """Stage and commit a receipt-backed batch that produced a sensitive fact; return the source id."""
    candidates = [
        {"type": "person", "ref": "ida", "name": "Ida Kerr", "aliases": []},
        {
            "type": "fact",
            "person_ref": "ida",
            "predicate": "health",
            "value": "Committed sensitive detail",
            "sensitivity": "sensitive",
        },
    ]
    input_path = tmp_path / "candidates.json"
    input_path.write_text(json.dumps(candidates), encoding="utf-8")
    db = ["--db", str(db_file)]
    staged = _cli_json(
        capsys,
        *db,
        "import",
        "stage-candidates",
        "--source",
        "allotment meeting",
        "--input",
        str(input_path),
        "--source-kind",
        "meeting_transcript",
        "--label",
        "Allotment meeting",
        *receipt,
        "--json",
    )
    _cli_json(capsys, *db, "import", "commit", staged["batch_id"], "--all", "--json")
    sources = _cli_json(capsys, *db, "sources", "--json")
    source_id = sources["sources"][0]["id"]
    assert isinstance(source_id, str)
    return source_id


@contextmanager
def _client(
    db_file: Path,
    *,
    include_sensitive: bool = False,
    on_done: Callable[[], None] = lambda: None,
    port: int = _PORT,
    base_url: str = _ORIGIN,
) -> Iterator[TestClient]:
    @contextmanager
    def open_runtime() -> Iterator[ApplicationRuntime]:
        # Opened on the TestClient's event-loop thread, exactly as the lifespan does under uvicorn.
        runtime = build_runtime(db_file)
        try:
            yield runtime
        finally:
            runtime.close()

    app = create_browse_app(
        open_runtime,
        token=_TOKEN,
        port=port,
        include_sensitive=include_sensitive,
        on_done=on_done,
        review_warning=REVIEW_DISCLOSURE_WARNING,
        sources_warning=SOURCES_DISCLOSURE_WARNING,
        review_lines=import_review_lines,
    )
    with TestClient(app, base_url=base_url, headers={TOKEN_HEADER: _TOKEN}) as client:
        yield client


def _assert_security_headers(headers: Any) -> str:
    csp = headers["content-security-policy"]
    match = re.fullmatch(r"default-src 'self'; script-src 'nonce-([^']+)'; style-src 'nonce-\1'", csp)
    assert match is not None, csp
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"
    return match.group(1)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "people.db"
    build_runtime(path).close()
    return path


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/", {TOKEN_HEADER: ""}),  # the token-free reload of the page
        ("/?token=wrong", {}),
        ("/api/people", {TOKEN_HEADER: ""}),
        ("/api/people", {TOKEN_HEADER: "wrong"}),
        (f"/?token={_TOKEN}", {"host": f"evil.example:{_PORT}"}),
        (f"/?token={_TOKEN}", {"host": f"localhost:{_PORT}"}),
        ("/api/people", {"host": "127.0.0.1:9999"}),
        ("/api/people", {"origin": "http://evil.example"}),
        ("/api/people", {"origin": "http://127.0.0.1:9999"}),
        ("/api/people", {"sec-fetch-site": "cross-site"}),
        ("/api/people", {"sec-fetch-site": "same-site"}),
        ("/api/nowhere", {TOKEN_HEADER: ""}),
    ],
)
def test_every_failed_check_gets_the_same_generic_refusal(
    db_file: Path, path: str, headers: dict[str, str]
) -> None:
    _seed_people(db_file)
    with _client(db_file) as client:
        response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert response.text == "Forbidden\n"
    assert _TOKEN not in response.text
    _assert_security_headers(response.headers)


def test_a_refused_post_is_not_acted_on(db_file: Path) -> None:
    calls: list[bool] = []
    with _client(db_file, on_done=lambda: calls.append(True)) as client:
        response = client.post("/api/done", headers={"origin": "http://evil.example"})
    assert response.status_code == 403
    assert calls == []


def test_the_page_is_one_self_contained_document_under_its_own_nonce(db_file: Path) -> None:
    with _client(db_file) as client:
        first = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: "", "sec-fetch-site": "none"})
        second = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""})
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/html")
    nonce = _assert_security_headers(first.headers)
    assert nonce != _assert_security_headers(second.headers)
    page = first.text
    assert re.findall(r"<script[^>]*>", page, re.IGNORECASE) == [f'<script nonce="{nonce}">']
    assert re.findall(r"<style[^>]*>", page, re.IGNORECASE) == [f'<style nonce="{nonce}">']
    assert "style=" not in page
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page and "document.write" not in page
    assert "http://" not in page and "https://" not in page and "src=" not in page
    # The page never echoes the token; its script reads it and drops it from history first.
    assert _TOKEN not in page
    assert page.index("history.replaceState") < page.index("fetch(")
    assert REVIEW_DISCLOSURE_WARNING in page and SOURCES_DISCLOSURE_WARNING in page
    # A detail view's Back returns to the page it was opened from, not to page one.
    assert 'button("Back to people", () => showPeople(cursor, back))' in page
    assert 'button("Back to sources", () => showSources(cursor, back))' in page


def test_the_people_list_pages_the_person_index(db_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_people(db_file)
    with _client(db_file) as client:
        first = client.get("/api/people", params={"limit": "1"})
        cursor = first.json()["next_cursor"]
        second = client.get("/api/people", params={"limit": "1", "cursor": cursor})
        refusals = [
            client.get("/api/people", params={"limit": limit}).json() for limit in ("0", "201", "many")
        ]
        stale = client.get("/api/people", params={"cursor": "garbage"})
    _assert_security_headers(first.headers)
    listing = _cli_json(capsys, "--db", str(db_file), "list", "--json")
    assert [p["id"] for p in first.json()["people"] + second.json()["people"]] == [
        p["id"] for p in listing["people"]
    ]
    assert first.json()["people"][0].keys() == listing["people"][0].keys()
    assert second.json()["next_cursor"] is None
    assert refusals == [{"error": "invalid_person_page_limit"}] * 3
    assert stale.status_code == 400 and stale.json() == {"error": "invalid_person_cursor"}


def test_the_people_list_defaults_to_fifty_and_never_exceeds_two_hundred(db_file: Path) -> None:
    conn = open_db(db_file)
    try:
        repository = SqlitePeopleRepository(conn)
        for index in range(201):
            repository.save_person(Person(canonical_name=f"Person {index:03d}"))
    finally:
        conn.close()
    with _client(db_file) as client:
        assert len(client.get("/api/people").json()["people"]) == 50
        assert len(client.get("/api/people", params={"limit": "200"}).json()["people"]) == 200


def test_a_markup_name_is_returned_as_data_not_markup(db_file: Path) -> None:
    ids = _seed_people(db_file)
    with _client(db_file) as client:
        listing = client.get("/api/people")
        brief = client.get("/api/person", params={"id": ids["toma"]})
    assert listing.headers["content-type"] == "application/json"
    assert _MARKUP_NAME in [p["canonical_name"] for p in listing.json()["people"]]
    assert brief.json()["person"]["canonical_name"] == _MARKUP_NAME


def test_sensitive_records_follow_the_process_elevation_only(db_file: Path) -> None:
    ids = _seed_people(db_file)
    with _client(db_file) as client:
        ordinary = client.get("/api/person", params={"id": ids["elena"], "include_sensitive": "true"})
    with _client(db_file, include_sensitive=True) as client:
        elevated = client.get("/api/person", params={"id": ids["elena"]})
    assert _SENSITIVE_VALUE not in ordinary.text
    assert "Gardener" in ordinary.text
    assert ordinary.json()["disclosure"]["context"] == "ordinary"
    assert _SENSITIVE_VALUE in elevated.text
    assert elevated.json()["disclosure"]["context"] == "sensitive"


def test_the_person_view_carries_the_brief_truncation_flag(
    db_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = _seed_people(db_file)
    with _client(db_file) as client:
        over = client.get("/api/person", params={"id": ids["toma"]}).json()
        under = client.get("/api/person", params={"id": ids["elena"]}).json()
        missing = client.get("/api/person", params={"id": "no-such-person"})
    assert over["truncated"] is True and len(over["facts"]) == BRIEF_CONTEXT_ITEMS
    assert under["truncated"] is False
    assert missing.status_code == 404 and missing.json() == {"error": "unknown_person"}
    cli_brief = _cli_json(capsys, "--db", str(db_file), "brief", ids["toma"], "--json")
    assert {k: v for k, v in cli_brief.items() if k != "generated_at"} == {
        k: v for k, v in over.items() if k != "generated_at"
    }


def test_the_sources_page_is_the_cli_listing_page(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_committed_source(db_file, tmp_path, capsys)
    _seed_committed_source(db_file, tmp_path, capsys)
    first_cli = _cli_json(capsys, "--db", str(db_file), "sources", "--limit", "1", "--json")
    second_cli = _cli_json(
        capsys, "--db", str(db_file), "sources", "--limit", "1", "--cursor", first_cli["next_cursor"], "--json"
    )
    capsys.readouterr()
    with _client(db_file) as client:
        first = client.get("/api/sources", params={"limit": "1"})
        second = client.get("/api/sources", params={"limit": "1", "cursor": first_cli["next_cursor"]})
        refused = client.get("/api/sources", params={"limit": "500"})
    _assert_security_headers(first.headers)
    assert first.json() == first_cli
    assert second.json() == second_cli
    assert refused.status_code == 400 and set(refused.json()) == {"error"}


def test_source_detail_exposes_staged_counts_and_no_committed_mappings(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_id = _seed_committed_source(db_file, tmp_path, capsys)
    shown = _cli_json(capsys, "--db", str(db_file), "source", "show", source_id, "--json")
    capsys.readouterr()
    with _client(db_file) as client:
        ordinary = client.get("/api/source", params={"id": source_id})
        missing = client.get("/api/source", params={"id": "no-such-source"})
    with _client(db_file, include_sensitive=True) as client:
        elevated = client.get("/api/source", params={"id": source_id})
    _assert_security_headers(ordinary.headers)
    body = ordinary.json()
    assert set(body) == {"source", "staged_total", "staged_by_status"}
    assert body["source"] == shown["source"]
    assert body["staged_total"] == shown["counts"]["staged_total"]
    assert body["staged_by_status"] == shown["counts"]["staged_by_status"]
    committed_ids = [mapping["entity_id"] for mapping in shown["mappings"] if mapping["entity_id"]]
    assert committed_ids
    for hidden in ["mappings", "mappings_total", "mappings_by_disposition", *committed_ids]:
        assert hidden not in ordinary.text
    assert ordinary.content == elevated.content
    assert missing.status_code == 404 and missing.json() == {"error": "unknown_source_session"}


def test_done_asks_the_process_to_stop(db_file: Path) -> None:
    calls: list[bool] = []
    with _client(db_file, on_done=lambda: calls.append(True)) as client:
        response = client.post("/api/done", headers={"origin": _ORIGIN, "sec-fetch-site": "same-origin"})
    assert response.status_code == 200
    _assert_security_headers(response.headers)
    assert calls == [True]


def test_an_unknown_route_is_only_reported_after_the_guard(db_file: Path) -> None:
    with _client(db_file) as client:
        response = client.get("/api/nowhere")
    assert response.status_code == 404
    _assert_security_headers(response.headers)


def test_an_internal_error_is_generic_and_carries_no_detail(
    db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_people(db_file)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Elena Marsh detail")

    with _client(db_file) as client:
        monkeypatch.setattr(type(client.app_state["runtime"].use_cases.compose_person_brief), "execute", explode)
        response = client.get("/api/person", params={"id": ids["elena"]})
    assert response.status_code == 500
    assert "Elena" not in response.text
    _assert_security_headers(response.headers)


def test_port_80_accepts_the_default_port_form_browsers_send(db_file: Path) -> None:
    with _client(db_file, port=80, base_url="http://127.0.0.1") as client:
        bare = client.get("/api/people", headers={"origin": "http://127.0.0.1"})
        explicit = client.get("/api/people", headers={"host": "127.0.0.1:80", "origin": "http://127.0.0.1:80"})
        foreign = client.get("/api/people", headers={"host": "127.0.0.1:8080"})
    assert bare.status_code == 200
    assert explicit.status_code == 200
    assert foreign.status_code == 403


def test_opaque_ids_containing_a_slash_open_their_person(db_file: Path) -> None:
    conn = open_db(db_file)
    try:
        SqlitePeopleRepository(conn).save_person(Person(id="restored/elena", canonical_name="Elena Marsh"))
    finally:
        conn.close()
    with _client(db_file) as client:
        listed = client.get("/api/people").json()["people"][0]["id"]
        brief = client.get("/api/person", params={"id": listed})
    assert listed == "restored/elena"
    assert brief.status_code == 200 and brief.json()["person"]["id"] == "restored/elena"


def test_a_forgotten_source_withholds_its_counts_rather_than_reporting_zero(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_id = _seed_committed_source(db_file, tmp_path, capsys, "--content-digest", "a" * 64)
    assert cli.main(["--db", str(db_file), "delete", "Ida Kerr", "--yes"]) == 0
    capsys.readouterr()
    with _client(db_file) as client:
        body = client.get("/api/source", params={"id": source_id}).json()
    assert body["source"]["redacted"] is True
    assert body["staged_total"] is None and body["staged_by_status"] is None


# --- M30.2 batch review ----------------------------------------------------------------------

_SAME_ORIGIN = {"origin": _ORIGIN, "sec-fetch-site": "same-origin"}


def _stage(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], candidates: list[Any]) -> str:
    input_path = tmp_path / "batch.json"
    input_path.write_text(json.dumps(candidates), encoding="utf-8")
    staged = _cli_json(
        capsys, "--db", str(db_file), "import", "stage-candidates", "--source", "allotment meeting",
        "--input", str(input_path), "--json",
    )
    batch_id = staged["batch_id"]
    assert isinstance(batch_id, str)
    return batch_id


def _elena_batch(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Elena with a sensitive fact, a markup-bearing fact, and a note to withdraw."""
    return _stage(
        db_file,
        tmp_path,
        capsys,
        [
            {"type": "person", "ref": "elena", "name": "Elena Marsh", "aliases": []},
            {"type": "fact", "person_ref": "elena", "predicate": "health", "value": _SENSITIVE_VALUE,
             "sensitivity": "sensitive"},
            {"type": "fact", "person_ref": "elena", "predicate": "role", "value": _MARKUP_NAME},
            {"type": "fact", "person_ref": "elena", "predicate": "note", "value": "Drop this one"},
        ],
    )


def _cli_review(capsys: pytest.CaptureFixture[str], db_file: Path, batch_id: str) -> dict[str, Any]:
    return _cli_json(capsys, "--db", str(db_file), "import", "review", batch_id, "--json")


def _ids(review: dict[str, Any]) -> dict[str, str]:
    """Candidate ids by person name or fact predicate."""
    return {
        row["candidate"].get("predicate") or row["candidate"]["name"]: row["id"] for row in review["candidates"]
    }


def _action(client: TestClient, verb: str, batch_id: str, ids: list[str], digest: str) -> Any:
    return client.post(
        f"/api/batch/{verb}",
        json={"batch_id": batch_id, "candidate_ids": ids, "expected_batch_digest": digest},
        headers=_SAME_ORIGIN,
    )


def test_the_batch_view_is_the_cli_review_verbatim(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    note = _ids(_cli_review(capsys, db_file, batch_id))["note"]
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, note]) == 0
    capsys.readouterr()
    expected = _cli_review(capsys, db_file, batch_id)
    with _client(db_file) as client:
        response = client.get("/api/batch", params={"id": batch_id})
        missing = client.get("/api/batch", params={"id": "no-such-batch"})
    _assert_security_headers(response.headers)
    body = response.json()
    assert body["review"] == expected
    assert [row["ordinal"] for row in body["review"]["candidates"]] == [1, 2, 3, 4]
    assert "rejected" in [row["status"] for row in body["review"]["candidates"]]
    rows = [ImportReviewRow.model_validate(row) for row in expected["candidates"]]
    assert body["lines"] == import_review_lines(rows)
    # Staged candidates are shown as `pctx import review` shows them: a sensitive one without
    # elevation, and a markup value as data.
    assert _SENSITIVE_VALUE in response.text
    assert _MARKUP_NAME in [row["candidate"].get("value") for row in body["review"]["candidates"]]
    assert missing.status_code == 404 and missing.json() == {"error": "batch_not_found"}


def test_withdrawing_with_the_displayed_digest_matches_the_cli(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        response = _action(client, "withdraw", batch_id, [_ids(shown)["note"]], shown["batch_digest"])
        reloaded = client.get("/api/batch", params={"id": batch_id}).json()["review"]
    _assert_security_headers(response.headers)
    assert response.status_code == 200
    fresh = _cli_review(capsys, db_file, batch_id)
    assert response.json() == {"withdrawn": 1, "batch_digest": fresh["batch_digest"]}
    assert reloaded == fresh
    assert {row["id"]: row["status"] for row in fresh["candidates"]}[_ids(shown)["note"]] == "rejected"


@pytest.mark.parametrize("change", ["amend_checked", "amend_unchecked_person", "commit_checked"])
def test_a_batch_changed_after_display_refuses_withdraw_and_commit(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    db = ["--db", str(db_file)]
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        ids = _ids(shown)
        checked = [ids["role"]]
        if change == "amend_checked":
            argv = ["import", "amend", batch_id, ids["role"], "--patch", '{"value": "Beekeeper"}']
        elif change == "amend_unchecked_person":
            argv = ["import", "amend", batch_id, ids["Elena Marsh"], "--patch", '{"name": "Elena Marsh-Ibarra"}']
        else:
            argv = ["import", "commit", batch_id, "--accept", ids["Elena Marsh"], ids["health"]]
        assert cli.main([*db, *argv]) == 0
        capsys.readouterr()
        before = _cli_review(capsys, db_file, batch_id)
        withdraw = _action(client, "withdraw", batch_id, checked, shown["batch_digest"])
        commit = _action(client, "commit", batch_id, checked, shown["batch_digest"])
    for response in (withdraw, commit):
        assert response.status_code == 409
        assert response.json() == {"error": "batch_changed"}
        _assert_security_headers(response.headers)
    assert _cli_review(capsys, db_file, batch_id) == before


def test_a_refusal_carries_the_use_case_code_and_never_the_payload(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        ids = _ids(shown)
        withdrawn = _action(client, "withdraw", batch_id, [ids["health"]], shown["batch_digest"]).json()
        commit = _action(client, "commit", batch_id, [ids["health"]], withdrawn["batch_digest"])
        foreign = _action(client, "withdraw", batch_id, ["not-in-this-batch"], withdrawn["batch_digest"])
    assert commit.status_code == 400 and commit.json() == {"error": "candidate_withdrawn"}
    assert foreign.status_code == 400 and set(foreign.json()) == {"error"}
    for response in (commit, foreign):
        assert ids["health"] not in response.text and "not-in-this-batch" not in response.text
        assert _SENSITIVE_VALUE not in response.text


def test_committing_reports_the_use_case_counts_and_matches_a_fresh_review(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        ids = _ids(shown)
        accepted = [ids["Elena Marsh"], ids["role"]]
        response = _action(client, "commit", batch_id, accepted, shown["batch_digest"])
        reloaded = client.get("/api/batch", params={"id": batch_id}).json()["review"]
    _assert_security_headers(response.headers)
    body = response.json()
    assert body["committed_ids"] == accepted
    assert body["unresolved_ids"] == [] and body["skipped_ids"] == []
    fresh = _cli_review(capsys, db_file, batch_id)
    assert reloaded == fresh
    statuses = {row["id"]: row["status"] for row in fresh["candidates"]}
    assert [statuses[ids[key]] for key in ("Elena Marsh", "role", "health", "note")] == [
        "committed", "committed", "pending", "pending",
    ]


def test_an_accepted_ambiguous_person_and_its_fact_are_both_unresolved(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = open_db(db_file)
    try:
        repository = SqlitePeopleRepository(conn)
        repository.save_person(Person(canonical_name="Toma Ibarra"))
        repository.save_person(Person(canonical_name="Toma Ibarra"))
    finally:
        conn.close()
    batch_id = _stage(
        db_file,
        tmp_path,
        capsys,
        [
            {"type": "person", "ref": "toma", "name": "Toma Ibarra", "aliases": []},
            {"type": "fact", "person_ref": "toma", "predicate": "role", "value": "Treasurer"},
        ],
    )
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        ids = _ids(shown)
        both = [ids["Toma Ibarra"], ids["role"]]
        response = _action(client, "commit", batch_id, both, shown["batch_digest"])
    assert shown["candidates"][0]["candidate"]["match_disposition"] == "ambiguous"
    assert response.status_code == 200
    assert response.json()["committed_ids"] == []
    assert sorted(response.json()["unresolved_ids"]) == sorted(both)


def test_a_second_connection_cannot_write_the_batch_while_the_commit_holds_it(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An amendment from elsewhere waits for the commit; it can never land between check and write."""
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    attempts: list[str] = []

    class _Contending:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            contender = open_db(db_file)
            contender.execute("PRAGMA busy_timeout = 0")
            try:
                contender.execute(
                    "UPDATE import_staging SET candidate_json = candidate_json WHERE batch_id = ?", (batch_id,)
                )
                attempts.append("written")
            except sqlite3.OperationalError as exc:
                attempts.append(str(exc))
            finally:
                contender.close()
            return self._inner.execute(*args, **kwargs)

    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        commit_import = client.app_state["runtime"].use_cases.commit_import
        commit_import._remember_person = _Contending(commit_import._remember_person)
        response = _action(client, "commit", batch_id, [_ids(shown)["Elena Marsh"]], shown["batch_digest"])
    assert attempts == ["database is locked"]
    assert response.status_code == 200 and len(response.json()["committed_ids"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"batch_id": "b", "candidate_ids": []}).encode(),
        json.dumps({"batch_id": "b", "candidate_ids": [], "expected_batch_digest": ""}).encode(),
        json.dumps({"batch_id": "b", "candidate_ids": "x", "expected_batch_digest": "d"}).encode(),
        json.dumps({"batch_id": "b", "candidate_ids": [], "expected_batch_digest": "d", "all": True}).encode(),
    ],
)
def test_a_malformed_action_is_refused_without_echoing_it(db_file: Path, body: bytes) -> None:
    with _client(db_file) as client:
        responses = [
            client.post(f"/api/batch/{verb}", content=body, headers=_SAME_ORIGIN) for verb in ("withdraw", "commit")
        ]
    for response in responses:
        assert response.status_code == 400 and response.json() == {"error": "invalid_request"}


def test_an_oversized_action_is_refused_before_it_is_parsed(
    db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_app, "MAX_BATCH_ACTION_BYTES", 64)
    body = json.dumps({"batch_id": "b", "candidate_ids": ["x" * 100], "expected_batch_digest": "d"})
    with _client(db_file) as client:
        response = client.post("/api/batch/commit", content=body, headers=_SAME_ORIGIN)
    assert response.status_code == 413 and response.json() == {"error": "request_too_large"}


def test_a_cross_origin_action_is_refused_and_writes_nothing(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    with _client(db_file) as client:
        for verb in ("withdraw", "commit"):
            response = client.post(
                f"/api/batch/{verb}",
                json={
                    "batch_id": batch_id,
                    "candidate_ids": [row["id"] for row in before["candidates"]],
                    "expected_batch_digest": before["batch_digest"],
                },
                headers={"origin": "http://evil.example"},
            )
            assert response.status_code == 403 and response.text == "Forbidden\n"
    assert _cli_review(capsys, db_file, batch_id) == before


def test_every_batch_endpoint_applies_the_cli_review_ceiling_first(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)

    def too_large(_batch_id: str) -> None:
        raise ImportPipelineError("batch_too_large_for_cli", "Elena Marsh batch is too large")

    with _client(db_file) as client:
        client.app_state["runtime"].use_cases.preflight_import_batch.execute = too_large
        read = client.get("/api/batch", params={"id": batch_id})
        writes = [
            _action(client, verb, batch_id, [before["candidates"][0]["id"]], before["batch_digest"])
            for verb in ("withdraw", "commit")
        ]
    for response in (read, *writes):
        assert response.status_code == 400 and response.json() == {"error": "batch_too_large_for_cli"}
    assert _cli_review(capsys, db_file, batch_id) == before


def test_the_batch_page_confirms_a_commit_by_count_and_offers_no_select_all(db_file: Path) -> None:
    with _client(db_file) as client:
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    assert "async function showBatch(" in page
    assert '"Confirm commit of " + count' in page
    assert "expected_batch_digest: batchState.review.batch_digest" in page
    # Accepting writes nothing, and nothing selects every row.
    assert "/api/batch/accept" not in page
    assert "select all" not in page.casefold() and "selectall" not in page.casefold()
    assert REVIEW_DISCLOSURE_WARNING in page


def test_a_late_response_never_renders_over_the_view_the_user_moved_to(db_file: Path) -> None:
    """A withdrawal or commit that completes after the user opened another view or batch is dropped.

    Its continuation refreshes the batch it was started on, captured before the request, and only
    while no navigation happened since; every view's own fetch is held to the same rule.
    """
    with _client(db_file) as client:
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    script = page[page.index("<script") :]
    for view in ("showPeople", "showPerson", "showSources", "showSource", "showBatch"):
        body = script[script.index(f"async function {view}(") :]
        body = body[: body.index("\n}\n")]
        assert "const at = navigate();" in body and "if (at !== navigation) return;" in body, view
    for action, refresh in (
        ("withdrawSelected", "showBatch(batchId, message, baseline)"),
        ("commitAccepted", "showBatch(batchId, message)"),
    ):
        body = script[script.index(f"async function {action}(") :]
        body = body[: body.index("\n}\n")]
        assert body.index("const at = navigate();") < body.index("await ")
        assert body.index("const batchId = batchState.id;") < body.index("await ")
        assert f"if (at === navigation) return {refresh};" in body
        assert "showBatch(batchState.id" not in body


# A DOM just large enough to run the page's own script under node, so a click sequence is exercised
# for real rather than inferred from the script text. Each write POST is held until released.
_DOM_HARNESS = r"""
class El { constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, textContent: "",
  disabled: false, checked: false }); }
  append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, handler) { this.listeners[event] = handler; } setAttribute() {}
  all() { const out = []; const walk = (n) => { for (const c of n.children || []) { out.push(c); walk(c); } };
    walk(this); return out; }
  querySelectorAll() { return this.all().filter((n) => n.tag === "button" || n.tag === "input"); } }
globalThis.Node = El;
const ids = {};
globalThis.document = { getElementById: (id) => (ids[id] ??= new El(id)), createElement: (t) => new El(t),
  createTextNode: (text) => ({ text }) };
globalThis.location = { search: "?token=t" };
globalThis.history = { replaceState() {} };
const posts = []; const releases = [];
const review = { batch_id: "B", batch_digest: "d1", candidates: [
  { id: "p1", status: "pending", ordinal: 1, candidate: { type: "person", name: "Elena Marsh" } }] };
globalThis.fetch = async (path, options) => {
  if (options && options.method === "POST") {
    posts.push(path); await new Promise((resolve) => releases.push(resolve));
    return { ok: true, json: async () => ({ withdrawn: 1, batch_digest: "d2", committed_ids: ["p1"],
      unresolved_ids: [], skipped_ids: [] }) };
  }
  if (path.startsWith("/api/batch")) return { ok: true, json: async () => ({ review, lines: ["#1"] }) };
  return { ok: true, json: async () => ({ people: [], next_cursor: null }) };
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 5));
"""

_DOUBLE_CLICKS = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const check = () => { view.all().find((n) => n.tag === "input").checked = true; };
  await showBatch("B"); check();
  const withdraw = find("Withdraw selected");
  withdraw.listeners.click(); withdraw.listeners.click();
  const withdrawPosts = posts.length;
  releases.forEach((r) => r()); await tick();
  check(); find("Accept selected").listeners.click(); await tick();
  find("Commit accepted").listeners.click();
  const confirm = find("Confirm commit of 1");
  confirm.listeners.click(); confirm.listeners.click();
  const commitPosts = posts.length - withdrawPosts;
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ withdrawPosts, commitPosts, message: batchState.message }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_double_click_sends_one_withdrawal_and_one_commit(db_file: Path, tmp_path: Path) -> None:
    with _client(db_file) as client:
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    script = re.search(r"<script[^>]*>(.*)</script>", page, re.DOTALL)
    assert script is not None
    program = tmp_path / "page.js"
    program.write_text(_DOM_HARNESS + script.group(1) + _DOUBLE_CLICKS, encoding="utf-8")
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run([node, str(program)], capture_output=True, text=True, timeout=30, check=True)
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome == {
        "withdrawPosts": 1,
        "commitPosts": 1,
        "message": "Committed 1; 0 unresolved; 0 already committed.",
    }
