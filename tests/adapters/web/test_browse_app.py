"""The `pctx browse` application: its guard, security headers, read endpoints (M30.1), and batch review (M30.2)."""

from __future__ import annotations

import json
import re
import shlex
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
from people_context.adapters.web.fields import ALIAS_FIELDS, CANDIDATE_FIELDS
from people_context.app.exports.brief import BRIEF_CONTEXT_ITEMS
from people_context.app.imports import ImportPipelineError, ImportReviewRow
from people_context.app.records import RecordFact, RecordFactInput, RecordInteractionInput, SetAffiliationInput
from people_context.cli.imports import REVIEW_DISCLOSURE_WARNING
from people_context.cli.rendering import import_review_lines
from people_context.cli.sources import SOURCES_DISCLOSURE_WARNING
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Sensitivity

_PORT = 8123
_ORIGIN = f"http://127.0.0.1:{_PORT}"
_TOKEN = "launch-token-for-elena"
_MARKUP_NAME = '<img src=x onerror="alert(1)">Toma'
_SENSITIVE_VALUE = "Private recovery detail"
#: Stands in for whatever a reviewer types into the edit form. A refusal names the field it
#: failed on; this must never come back in one.
_SENTINEL = "ZmarshQUARANTINE"


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


def _seed_committed_source(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *receipt: str) -> str:
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
    language: str = "en",
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
        language=language,
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
def test_every_failed_check_gets_the_same_generic_refusal(db_file: Path, path: str, headers: dict[str, str]) -> None:
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
    assert 'button(t("person.back"), () => showPeople(cursor, back))' in page
    assert 'button(t("sources.back"), () => showSources(cursor, back))' in page


def test_the_people_list_pages_the_person_index(db_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_people(db_file)
    with _client(db_file) as client:
        first = client.get("/api/people", params={"limit": "1"})
        cursor = first.json()["next_cursor"]
        second = client.get("/api/people", params={"limit": "1", "cursor": cursor})
        refusals = [client.get("/api/people", params={"limit": limit}).json() for limit in ("0", "201", "many")]
        stale = client.get("/api/people", params={"cursor": "garbage"})
    _assert_security_headers(first.headers)
    listing = _cli_json(capsys, "--db", str(db_file), "list", "--json")
    assert [p["id"] for p in first.json()["people"] + second.json()["people"]] == [p["id"] for p in listing["people"]]
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


def test_the_people_list_searches_orders_and_filters_through_the_person_index(db_file: Path) -> None:
    ids = _seed_people(db_file)
    runtime = build_runtime(db_file)
    try:
        runtime.use_cases.set_affiliation.execute(
            SetAffiliationInput(person_id=ids["elena"], org="Allotment Society", role="treasurer")
        )
    finally:
        runtime.close()
    with _client(db_file) as client:
        everyone = client.get("/api/people").json()
        organization = everyone["filters"]["organizations"][0]
        searched = client.get("/api/people", params={"q": "ALLOTMENT"}).json()  # Elena's summary
        filtered = client.get("/api/people", params={"organization": organization["id"]}).json()
        newest = client.get("/api/people", params={"sort": "created"}).json()
        unknown = client.get("/api/people", params={"sort": "shoe size"})
    assert everyone["filters"] == {"organizations": [organization], "groups": []}
    assert everyone["organization_names"] == {ids["elena"]: ["Allotment Society"]}
    assert organization["name"] == "Allotment Society"
    assert [p["id"] for p in searched["people"]] == [ids["elena"]]
    assert [p["id"] for p in filtered["people"]] == [ids["elena"]]
    # Toma was saved after Elena, so the newest-first order puts Toma first.
    assert [p["id"] for p in newest["people"]] == [ids["toma"], ids["elena"]]
    assert (unknown.status_code, unknown.json()) == (400, {"error": "invalid_person_sort"})


_FILTER_PEOPLE = r"""
(async () => {
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  globalThis.peopleDoc = { people: [], next_cursor: "C1",
    filters: { organizations: [{ id: "O1", name: "Presight" }], groups: [] } };
  await showPeople(null, []);
  const groupOffered = labelled("filter by group") !== undefined;
  view.all().find((n) => n.tag === "button" && n.textContent === "Next").listeners.click();
  await tick();
  const search = labelled("search people");
  search.value = "  moft ";
  search.listeners.change();
  await tick();
  const organization = labelled("filter by organization");
  organization.value = "O1";
  organization.listeners.change();
  await tick();
  const sort = labelled("sort people");
  sort.value = "updated";
  sort.listeners.change();
  await tick();
  // Unchanged: no new request.
  labelled("search people").listeners.change();
  await tick();
  console.log(JSON.stringify({ groupOffered, gets, notice: view.all().map((n) => n.textContent).filter((t) => t)
    .includes("No people match.") }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_changing_a_filter_refetches_from_the_first_page(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _FILTER_PEOPLE)
    initial = outcome["gets"][:1]  # the page's own first load
    assert outcome["groupOffered"] is False
    assert outcome["gets"][len(initial) :] == [
        "/api/people?sort=name",
        "/api/people?cursor=C1&sort=name",
        "/api/people?q=moft&sort=name",
        "/api/people?q=moft&sort=name&organization=O1",
        "/api/people?q=moft&sort=updated&organization=O1",
    ]
    assert outcome["notice"] is True


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


def test_the_person_view_carries_the_brief_truncation_flag(db_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ids = _seed_people(db_file)
    with _client(db_file) as client:
        over = client.get("/api/person", params={"id": ids["toma"]}).json()
        under = client.get("/api/person", params={"id": ids["elena"]}).json()
        missing = client.get("/api/person", params={"id": "no-such-person"})
    assert over["truncated"] is True and len(over["facts"]) == BRIEF_CONTEXT_ITEMS
    assert under["truncated"] is False
    assert missing.status_code == 404 and missing.json() == {"error": "unknown_person"}
    cli_brief = _cli_json(capsys, "--db", str(db_file), "brief", ids["toma"], "--json")
    # The page's view is the brief exactly, plus the editor keys that ride beside it.
    page_only = {"generated_at", "alias_details", "alias_kinds", "record_fields"}
    assert {k: v for k, v in cli_brief.items() if k != "generated_at"} == {
        k: v for k, v in over.items() if k not in page_only
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


def test_an_internal_error_is_generic_and_carries_no_detail(db_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        capsys,
        "--db",
        str(db_file),
        "import",
        "stage-candidates",
        "--source",
        "allotment meeting",
        "--input",
        str(input_path),
        "--json",
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
            {
                "type": "fact",
                "person_ref": "elena",
                "predicate": "health",
                "value": _SENSITIVE_VALUE,
                "sensitivity": "sensitive",
            },
            {"type": "fact", "person_ref": "elena", "predicate": "role", "value": _MARKUP_NAME},
            {"type": "fact", "person_ref": "elena", "predicate": "note", "value": "Drop this one"},
        ],
    )


def _cli_review(capsys: pytest.CaptureFixture[str], db_file: Path, batch_id: str) -> dict[str, Any]:
    return _cli_json(capsys, "--db", str(db_file), "import", "review", batch_id, "--json")


def _ids(review: dict[str, Any]) -> dict[str, str]:
    """Candidate ids by person name or fact predicate."""
    return {row["candidate"].get("predicate") or row["candidate"]["name"]: row["id"] for row in review["candidates"]}


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
        "committed",
        "committed",
        "pending",
        "pending",
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


def test_an_oversized_action_is_refused_before_it_is_parsed(db_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert 't("batch.commit_confirm_button", { count })' in page and '"Confirm commit of {count}"' in page
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
    for view in ("showPeople", "showPerson", "showSources", "showSource", "showBatch", "done"):
        body = script[script.index(f"async function {view}(") :]
        body = body[: body.index("\n}\n")]
        assert "const at = navigate();" in body and "if (at !== navigation) return;" in body, view
    for action, refresh in (
        ("withdrawSelected", "showBatch(batchId, message, baseline)"),
        ("commitAccepted", "showBatch(batchId, message)"),
        ("amendRow", "showBatch(batchId, message, result.batch_digest)"),
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
const NL = [String.fromCharCode(10), String.fromCharCode(13)];
class El { constructor(tag) { Object.assign(this, { tag, children: [], listeners: {}, textContent: "",
  disabled: false, checked: false, selected: false, attrs: {} }); this._value = ""; }
  // A real `<input type="text">` sanitises its value by dropping CR and LF. Modelled here so a
  // control that would flatten a staged multiline string fails the test instead of the reviewer.
  set value(v) { let s = v === undefined || v === null ? "" : String(v);
    if (this.tag === "input" && this.type === "text") for (const ch of NL) s = s.split(ch).join("");
    this._value = s; }
  get value() { return this._value; }
  append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this.children = nodes; }
  removeChild(node) { this.children = this.children.filter((child) => child !== node); }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  setAttribute(name, value) { this.attrs[name] = value; }
  get selectedOptions() { return this.children.filter((child) => child.selected); }
  all() { const out = []; const walk = (n) => { for (const c of n.children || []) { out.push(c); walk(c); } };
    walk(this); return out; }
  querySelectorAll() {
    return this.all().filter((n) => ["button", "input", "select", "textarea"].includes(n.tag)); } }
globalThis.Node = El;
const ids = {};
globalThis.document = { getElementById: (id) => (ids[id] ??= new El(id)), createElement: (t) => new El(t),
  createTextNode: (text) => ({ text }) };
globalThis.location = { search: "?token=t" };
globalThis.history = { replaceState() {} };
const posts = []; const releases = []; const heldGets = []; let holdGets = false;
const review = { batch_id: "B", batch_digest: "d1", candidates: [
  { id: "p1", status: "pending", ordinal: 1, candidate: { type: "person", name: "Elena Marsh" } }] };
// One row, shaped per scenario; and one POST made to fail, for the refusal paths.
globalThis.setRows = (rows) => { review.candidates = rows; };
globalThis.setRow = (row) => {
  review.candidates = [Object.assign({ id: "p1", status: "pending", ordinal: 1 }, row)];
};
let postFailure = null;
globalThis.failNextPost = (status, body) => { postFailure = { status, body }; };
const gets = [];
globalThis.fetch = async (path, options) => {
  if (options && options.method === "POST") {
    posts.push({ path, body: options.body }); await new Promise((resolve) => releases.push(resolve));
    if (postFailure) {
      const failed = postFailure; postFailure = null;
      return { ok: false, status: failed.status, json: async () => failed.body };
    }
    return { ok: true, json: async () => ({ withdrawn: 1, batch_digest: "d2", committed_ids: ["p1"],
      unresolved_ids: [], skipped_ids: [] }) };
  }
  gets.push(path);
  if (holdGets) await new Promise((resolve) => heldGets.push(resolve));
  if (path.startsWith("/api/batch")) {
    return { ok: true, json: async () => ({ review, lines: review.candidates.map((row) => "#" + row.ordinal),
      fields: __FIELDS__,
      alias_fields: __ALIAS_FIELDS__ }) };
  }
  if (path.startsWith("/api/person?")) return { ok: true, json: async () => globalThis.personDoc };
  return { ok: true, json: async () => globalThis.peopleDoc || { people: [], next_cursor: null } };
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


_DONE_WHILE_LOADING = r"""
(async () => {
  await tick();
  holdGets = true;
  showBatch("B");
  const stopping = done();
  releases.forEach((r) => r()); await stopping;
  holdGets = false; heldGets.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ shown: view.children.map((n) => n.textContent) }));
})();
"""


# Opening one row's form, changing one field, and saving it: the patch names only what moved, and
# carries the digest of the review on screen.
_EDIT_ONE_FIELD = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  await showBatch("B");
  find("Edit").listeners.click();
  const untouched = labelled("summary");
  labelled("name").value = "Elena Marshe";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ posts: posts.map((p) => [p.path, JSON.parse(p.body)]),
    untouched: untouched.value, message: batchState.message, editing: batchState.editing }));
})();
"""


# The five behaviours the first Codex review found missing, each driven through the real script.
_STALE_EDIT_RELOADS = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  await showBatch("B");
  find("Edit").listeners.click();
  labelled("name").value = "Elena Marshe";
  failNextPost(409, { error: "batch_changed" });
  const before = gets.length;
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ refetched: gets.length - before, message: batchState.message,
    digest: batchState.review.batch_digest, stillOpen: batchState.editing }));
})();
"""

_AMEND_DISCARDS_ACCEPTANCES = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  await showBatch("B");
  view.all().find((n) => n.tag === "input" && n.attrs["aria-label"] === "Select #1").checked = true;
  find("Accept selected").listeners.click(); await tick();
  const accepted = batchState.accepted.size;
  find("Edit").listeners.click();
  labelled("name").value = "Elena Marshe";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ accepted, after: batchState.accepted.size, message: batchState.message }));
})();
"""

_RESOLVED_ROW_KEEPS_A_PICKER = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  // A row the reviewer already resolved: `match_candidates` is no longer projected for it.
  setRow({ candidate: { type: "person", name: "Elena Marsh", matched_person_id: "person-1",
    match_disposition: "matched", match_count: 1 } });
  await showBatch("B");
  find("Edit").listeners.click();
  const select = labelled("Matching person");
  const options = select.children.map((o) => o.value);
  const opensOn = select.value;
  select.value = "";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ options, opensOn, patch: JSON.parse(posts[0].body).patch }));
})();
"""

_PASTED_ID_IS_VERBATIM = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  setRow({ candidate: { type: "person", name: "Elena Marsh", matched_person_id: null,
    match_disposition: "ambiguous", match_count: 12 },
    match_candidates: [{ id: "person-1", canonical_name: "Elena Marsh", name_truncated: false }],
    match_candidates_truncated: true });
  await showBatch("B");
  find("Edit").listeners.click();
  labelled("Person id").value = "  spaced-restored-id  ";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ patch: JSON.parse(posts[0].body).patch }));
})();
"""

_MULTILINE_SURVIVES = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  const prose = "Plans the allotment." + String.fromCharCode(10) + "Second line.";
  setRow({ candidate: { type: "person", name: "Elena Marsh", summary: prose } });
  await showBatch("B");
  find("Edit").listeners.click();
  const summary = labelled("summary");
  labelled("name").value = "Elena Marshe";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ tag: summary.tag, kept: summary.value === prose,
    patch: JSON.parse(posts[0].body).patch }));
})();
"""

_ONE_SAVE_CARRIES_BOTH = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  setRow({ candidate: { type: "person", name: "Elena Marsh", summary: "Allotment", matched_person_id: null,
    match_disposition: "ambiguous", match_count: 2 },
    match_candidates: [{ id: "person-1", canonical_name: "Elena Marsh", name_truncated: false },
      { id: "person-2", canonical_name: "Elena Marsh", name_truncated: false }] });
  await showBatch("B");
  find("Edit").listeners.click();
  labelled("summary").value = "Runs the allotment";
  labelled("Matching person").value = "person-2";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  const both = JSON.parse(posts[0].body).patch;
  // A picker nobody moved must stay out of the patch: naming the field reopens identity.
  find("Edit").listeners.click();
  labelled("summary").value = "Runs the allotment club";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ both, untouched: JSON.parse(posts[1].body).patch }));
})();
"""


_ACCEPT_KEEPS_THE_OPEN_FORM = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  setRow({ candidate: { type: "person", name: "Elena Marsh", summary: "Allotment" } });
  await showBatch("B");
  find("Edit").listeners.click();
  labelled("name").value = "Elena Marshe";
  // Accepting writes nothing, but it refetches and re-renders the batch around the open form.
  view.all().find((n) => n.tag === "input" && n.attrs["aria-label"] === "Select #1").checked = true;
  find("Accept selected").listeners.click(); await tick();
  const kept = labelled("name").value;
  const enabled = !labelled("name").disabled;
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ kept, enabled, patch: JSON.parse(posts[0].body).patch }));
})();
"""


_SHELL_COMMAND = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  setRow({ id: "cand'1", candidate: { type: "trait", person_candidate_id: "p9", category: "preference",
    value: "Mornings", evidence_note: "Said so", confidence: 0.6,
    evidence_ids: ["obs-1", "it's-opaque"] } });
  await showBatch("B");
  find("Edit").listeners.click();
  const shown = view.all().filter((n) => typeof n.textContent === "string"
    && n.textContent.startsWith("Change with: "));
  console.log(JSON.stringify({ command: shown[0].textContent.slice("Change with: ".length) }));
})();
"""


_CONTACT_IS_NOT_A_NAME = r"""
(async () => {
  const texts = (node) => node.all().map((n) => n.textContent || n.text).filter((t) => typeof t === "string" && t);
  const person = { id: "P", canonical_name: "Jordan Lee", aliases: ["Jordy", "jordan@example.com"],
    emails: ["jordan@example.com"], summary: null, is_self: false };
  globalThis.peopleDoc = { people: [person], next_cursor: null, organization_names: { P: ["Acme", "Presight"] } };
  await showPeople(null, []);
  const row = view.all().find((n) => n.tag === "tr" && texts(n).includes("Jordan Lee"));
  globalThis.personDoc = { person, disclosure: { context: "ordinary" }, affiliations: [], facts: [],
    interactions: [], reminders: [], traits: [], alias_kinds: ["nickname", "handle"],
    alias_details: [{ value: "Jordy", kind: "nickname" }, { value: "jordan@example.com", kind: "handle" }] };
  await showPerson("P", null, []);
  const metadata = view.children.filter((n) => n.tag === "dl")[1];
  const valueFor = (label) => metadata.children[metadata.children.findIndex(
    (n) => n.tag === "dt" && n.textContent === label) + 1];
  console.log(JSON.stringify({ row: texts(row), aliases: texts(valueFor("Aliases")),
    contact: texts(valueFor("Contact")) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_email_addresses_are_shown_as_contact_rather_than_aliases(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _CONTACT_IS_NOT_A_NAME)
    assert "jordan@example.com" not in " ".join(outcome["aliases"])
    assert "Jordy" in " ".join(outcome["aliases"])
    assert "jordan@example.com" in " ".join(outcome["contact"])
    # The people list has no contact column; the address appears only under Contact on the person view.
    assert outcome["row"] == ["Jordan Lee", "Jordan Lee", "Acme, Presight", "Jordy"]


def _run_page(db_file: Path, tmp_path: Path, scenario: str, *, language: str = "en") -> Any:
    """Run the served page's own script under node with the DOM harness and return its last line."""
    with _client(db_file, language=language) as client:
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    # The page's one inline script, sliced out rather than matched: this reads our own document.
    start = page.index(">", page.index("<script")) + 1
    script = page[start : page.index("</script>", start)]
    program = tmp_path / "page.js"
    harness = _DOM_HARNESS.replace("__FIELDS__", json.dumps(CANDIDATE_FIELDS)).replace(
        "__ALIAS_FIELDS__", json.dumps(ALIAS_FIELDS)
    )
    program.write_text(harness + script + scenario, encoding="utf-8")
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run([node, str(program)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_batch_response_after_done_does_not_repaint_the_stopped_page(db_file: Path, tmp_path: Path) -> None:
    assert _run_page(db_file, tmp_path, _DONE_WHILE_LOADING) == {
        "shown": ["pctx browse has stopped. You can close this tab."]
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_double_click_sends_one_withdrawal_and_one_commit(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _DOUBLE_CLICKS)
    assert outcome == {
        "withdrawPosts": 1,
        "commitPosts": 1,
        "message": "Committed 1; 0 unresolved; 0 already committed.",
    }


# --- M30.3 inline edit ------------------------------------------------------------------------


def _amend(client: TestClient, batch_id: str, candidate_id: str, patch: dict[str, Any], digest: str) -> Any:
    return client.post(
        "/api/batch/amend",
        json={
            "batch_id": batch_id,
            "candidate_id": candidate_id,
            "patch": patch,
            "expected_batch_digest": digest,
        },
        headers=_SAME_ORIGIN,
    )


def _candidates(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Stored candidates by the same key `_ids` uses."""
    return {
        row["candidate"].get("predicate") or row["candidate"].get("name") or row["candidate"]["type"]: row["candidate"]
        for row in review["candidates"]
    }


def _tracked_batch(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], candidates: list[Any]) -> str:
    """Stage a source-tracked batch, which same-batch evidence citation requires."""
    input_path = tmp_path / "tracked.json"
    input_path.write_text(json.dumps(candidates), encoding="utf-8")
    staged = _cli_json(
        capsys,
        "--db",
        str(db_file),
        "import",
        "stage-candidates",
        "--source",
        "allotment meeting",
        "--input",
        str(input_path),
        "--source-kind",
        "meeting_transcript",
        "--json",
    )
    batch_id = staged["batch_id"]
    assert isinstance(batch_id, str)
    return batch_id


def test_a_valid_edit_is_visible_to_the_cli_and_reports_the_new_digest(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        response = _amend(
            client, batch_id, _ids(shown)["role"], {"value": "Allotment secretary"}, shown["batch_digest"]
        )
        reloaded = client.get("/api/batch", params={"id": batch_id}).json()["review"]
    _assert_security_headers(response.headers)
    assert response.status_code == 200
    fresh = _cli_review(capsys, db_file, batch_id)
    assert response.json() == {"batch_digest": fresh["batch_digest"]}
    assert response.json()["batch_digest"] != shown["batch_digest"]
    assert reloaded == fresh
    assert _candidates(fresh)["role"]["value"] == "Allotment secretary"


def test_the_batch_read_carries_the_field_list_the_form_is_built_from(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        body = client.get("/api/batch", params={"id": batch_id}).json()
    assert body["fields"] == CANDIDATE_FIELDS
    assert body["alias_fields"] == ALIAS_FIELDS


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"value": ""}, "value"),
        ({"value": None}, "value"),
        ({"sensitivity": "confidential"}, "sensitivity"),
        ({"valid_from": "2026-06-01", "valid_to": "2026-01-01"}, "candidate"),
        ({"type": "observation"}, "type"),
        ({"match_disposition": "matched"}, "match_disposition"),
    ],
)
def test_an_invalid_edit_is_refused_against_the_field_it_names(
    db_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    patch: dict[str, Any],
    field: str,
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    with _client(db_file) as client:
        response = _amend(client, batch_id, _ids(before)["role"], patch, before["batch_digest"])
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_candidates"
    assert [entry["field"] for entry in body["fields"]] == [field]
    assert all(isinstance(entry["message"], str) and entry["message"] for entry in body["fields"])
    # Nothing was written: the batch is byte-identical, digest included.
    assert _cli_review(capsys, db_file, batch_id) == before


@pytest.mark.parametrize(
    "patch",
    [
        {"value": "", "stated_by": _SENTINEL},
        {"predicate": _SENTINEL, "value": ""},
        {_SENTINEL: "x"},
        {"sensitivity": _SENTINEL},
        {"value": _SENTINEL * 4096},
        {"valid_from": _SENTINEL},
    ],
)
def test_a_refused_edit_never_repeats_what_was_submitted(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], patch: dict[str, Any]
) -> None:
    """A patch is untrusted text; a refusal names the field and says nothing about the value."""
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    with _client(db_file) as client:
        response = _amend(client, batch_id, _ids(before)["role"], patch, before["batch_digest"])
    assert response.status_code >= 400
    assert _SENTINEL not in response.text
    assert _cli_review(capsys, db_file, batch_id) == before


def test_editing_one_alias_leaves_another_alias_language_and_script_alone(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _stage(
        db_file,
        tmp_path,
        capsys,
        [
            {
                "type": "person",
                "ref": "elena",
                "name": "Elena Marsh",
                "aliases": [
                    {"value": "Lena", "kind": "nickname", "lang": "en", "script": "Latn"},
                    {"value": "Елена", "kind": "native_script", "lang": "ru", "script": "Cyrl"},
                ],
            }
        ],
    )
    before = _cli_review(capsys, db_file, batch_id)
    staged = _candidates(before)["Elena Marsh"]["aliases"]
    # What the form sends: the untouched row verbatim, the edited row with its own metadata kept.
    edited = [{**staged[0], "value": "Lenny"}, {**staged[1]}]
    with _client(db_file) as client:
        response = _amend(client, batch_id, _ids(before)["Elena Marsh"], {"aliases": edited}, before["batch_digest"])
    assert response.status_code == 200
    aliases = _candidates(_cli_review(capsys, db_file, batch_id))["Elena Marsh"]["aliases"]
    assert aliases[0] == {"value": "Lenny", "kind": "nickname", "lang": "en", "script": "Latn"}
    assert aliases[1] == staged[1]


def test_participants_and_evidence_references_can_be_corrected_within_the_batch(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _tracked_batch(
        db_file,
        tmp_path,
        capsys,
        [
            {"type": "person", "ref": "elena", "name": "Elena Marsh", "aliases": []},
            {"type": "person", "ref": "toma", "name": "Toma Ibarra", "aliases": []},
            {
                "type": "interaction",
                "evidence_ref": "meet",
                "summary": "Allotment rota",
                "participant_refs": ["elena"],
                "date": "2026-06-01T10:00:00Z",
            },
            {"type": "observation", "evidence_ref": "seen", "person_ref": "elena", "text": "Brought seedlings"},
            {
                "type": "trait",
                "person_ref": "elena",
                "category": "preference",
                "value": "Mornings",
                "evidence_note": "Said so",
                "confidence": 0.6,
                "evidence_refs": ["seen"],
            },
        ],
    )
    before = _cli_review(capsys, db_file, batch_id)
    ids = {row["candidate"]["type"]: row["id"] for row in before["candidates"]}
    people = [row["id"] for row in before["candidates"] if row["candidate"]["type"] == "person"]
    with _client(db_file) as client:
        both = _amend(
            client, batch_id, ids["interaction"], {"participant_candidate_ids": people}, before["batch_digest"]
        )
        after_participants = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        evidence = _amend(
            client,
            batch_id,
            ids["trait"],
            {"evidence_candidate_ids": [ids["interaction"], ids["observation"]]},
            after_participants["batch_digest"],
        )
        after_evidence = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        # A reference of the wrong type is what the selector never offers, and the use case refuses.
        wrong = _amend(
            client,
            batch_id,
            ids["trait"],
            {"evidence_candidate_ids": [ids["person"]]},
            after_evidence["batch_digest"],
        )
    assert both.status_code == 200 and evidence.status_code == 200
    stored = {row["id"]: row["candidate"] for row in _cli_review(capsys, db_file, batch_id)["candidates"]}
    assert stored[ids["interaction"]]["participant_candidate_ids"] == people
    assert stored[ids["trait"]]["evidence_candidate_ids"] == [ids["interaction"], ids["observation"]]
    assert wrong.status_code == 400 and wrong.json()["error"] == "candidate_reference_invalid"
    assert "fields" not in wrong.json()


def test_an_edit_from_a_stale_view_is_refused_and_changes_nothing(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        # Another client amends a different row between the page's read and its save.
        assert (
            cli.main(
                [
                    "--db",
                    str(db_file),
                    "import",
                    "amend",
                    batch_id,
                    _ids(shown)["note"],
                    "--patch",
                    json.dumps({"value": "Keep this one"}),
                ]
            )
            == 0
        )
        capsys.readouterr()
        elsewhere = _cli_review(capsys, db_file, batch_id)
        response = _amend(
            client, batch_id, _ids(shown)["role"], {"value": "Allotment secretary"}, shown["batch_digest"]
        )
    assert response.status_code == 409
    assert response.json() == {"error": "batch_changed"}
    assert _cli_review(capsys, db_file, batch_id) == elsewhere


@pytest.mark.parametrize("closed", ["rejected", "committed"])
def test_a_closed_row_is_not_editable(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], closed: str
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    review = _cli_review(capsys, db_file, batch_id)
    target = _ids(review)["note"] if closed == "rejected" else _ids(review)["Elena Marsh"]
    verb = (
        ["import", "reject", batch_id, target]
        if closed == "rejected"
        else ["import", "commit", batch_id, "--accept", target]
    )
    assert cli.main(["--db", str(db_file), *verb]) == 0
    capsys.readouterr()
    before = _cli_review(capsys, db_file, batch_id)
    assert {row["id"]: row["status"] for row in before["candidates"]}[target] == closed
    with _client(db_file) as client:
        response = _amend(client, batch_id, target, {"value": "anything"}, before["batch_digest"])
    assert response.status_code == 400
    assert response.json() == {"error": "candidate_not_pending"}
    assert _cli_review(capsys, db_file, batch_id) == before


def _ambiguous_batch(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Two recorded Elena Marshes, and a person candidate that resolves to both."""
    conn = open_db(db_file)
    try:
        repository = SqlitePeopleRepository(conn)
        for summary in ("Plans the allotment", "Runs the choir"):
            repository.save_person(Person(canonical_name="Elena Marsh", summary=summary))
        conn.commit()
    finally:
        conn.close()
    return _stage(
        db_file,
        tmp_path,
        capsys,
        [{"type": "person", "ref": "elena", "name": "Elena Marsh", "aliases": []}],
    )


def test_the_ambiguity_picker_records_its_choice_through_amendment(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _ambiguous_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        row = shown["candidates"][0]
        # The picker's options are the review's own projection, which the page already holds.
        assert row["candidate"]["match_disposition"] == "ambiguous"
        assert len(row["match_candidates"]) == 2
        chosen = row["match_candidates"][1]["id"]
        response = _amend(client, batch_id, row["id"], {"matched_person_id": chosen}, shown["batch_digest"])
        after = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        stranger = _amend(client, batch_id, row["id"], {"matched_person_id": "person-nobody"}, after["batch_digest"])
    assert response.status_code == 200
    fresh = _cli_review(capsys, db_file, batch_id)
    assert fresh["candidates"][0]["candidate"]["matched_person_id"] == chosen
    assert fresh["candidates"][0]["candidate"]["match_disposition"] == "matched"
    assert stranger.status_code == 400 and stranger.json() == {"error": "person_not_a_match"}


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b'{"batch_id": "b", "candidate_id": "c", "patch": {}}',
        b'{"batch_id": "b", "candidate_id": "c", "patch": [], "expected_batch_digest": "d"}',
        b'{"batch_id": "b", "candidate_id": "", "patch": {}, "expected_batch_digest": "d"}',
        b'{"batch_id": "b", "candidate_id": "c", "patch": {}, "expected_batch_digest": "d", "x": 1}',
    ],
)
def test_a_malformed_edit_is_refused_without_echoing_it(db_file: Path, body: bytes) -> None:
    with _client(db_file) as client:
        response = client.post(
            "/api/batch/amend",
            content=body,
            headers={**_SAME_ORIGIN, "content-type": "application/json"},
        )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}


def test_an_oversized_edit_is_refused_before_it_is_parsed(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    payload = {
        "batch_id": batch_id,
        "candidate_id": _ids(before)["role"],
        "patch": {"value": _SENTINEL * web_app.MAX_BATCH_ACTION_BYTES},
        "expected_batch_digest": before["batch_digest"],
    }
    with _client(db_file) as client:
        response = client.post("/api/batch/amend", json=payload, headers=_SAME_ORIGIN)
    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}
    assert _SENTINEL not in response.text
    assert _cli_review(capsys, db_file, batch_id) == before


def test_a_cross_origin_edit_is_refused_and_writes_nothing(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    with _client(db_file) as client:
        response = client.post(
            "/api/batch/amend",
            json={
                "batch_id": batch_id,
                "candidate_id": _ids(before)["role"],
                "patch": {"value": "Allotment secretary"},
                "expected_batch_digest": before["batch_digest"],
            },
            headers={"origin": "http://evil.example"},
        )
    assert response.status_code == 403 and response.text == "Forbidden\n"
    assert _cli_review(capsys, db_file, batch_id) == before


def test_the_edit_endpoint_applies_the_cli_review_ceiling_first(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preflight every batch endpoint runs, so an unreviewable batch is not edited either."""
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    before = _cli_review(capsys, db_file, batch_id)
    refusal = ImportPipelineError("batch_too_large_for_cli", "too large", batch_id=batch_id)
    with _client(db_file) as client, pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            client.app_state["runtime"].use_cases.preflight_import_batch,
            "execute",
            lambda _batch_id: (_ for _ in ()).throw(refusal),
        )
        response = _amend(client, batch_id, _ids(before)["role"], {"value": "x"}, before["batch_digest"])
    assert response.status_code == 400
    assert response.json() == {"error": "batch_too_large_for_cli"}
    assert _cli_review(capsys, db_file, batch_id) == before


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_saving_one_field_sends_only_that_field_with_the_displayed_digest(db_file: Path, tmp_path: Path) -> None:
    """The form is generated from the real descriptors, so this exercises the page's own controls."""
    outcome = _run_page(db_file, tmp_path, _EDIT_ONE_FIELD)
    assert outcome == {
        "posts": [
            [
                "/api/batch/amend",
                {
                    "batch_id": "B",
                    "candidate_id": "p1",
                    "patch": {"name": "Elena Marshe"},
                    "expected_batch_digest": "d1",
                },
            ]
        ],
        # A field the reviewer never touched is not sent, and keeps what was staged.
        "untouched": "",
        "message": "Amended #1. Saved, not accepted.",
        "editing": None,
    }


def test_the_form_edits_staged_rows_only_and_authors_nothing(db_file: Path) -> None:
    """The browser edits what was staged; it does not author candidates or touch durable records."""
    with _client(db_file) as client:
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    script = page[page.index("<script") :]
    # The one write the form makes, always with the digest of the review it was built from.
    assert '"/api/batch/amend"' in script
    assert "expected_batch_digest: batchState.review.batch_digest" in script
    # No endpoint for creating a candidate; durable records are edited only from the person view.
    for absent in ("/api/batch/stage", "/api/batch/add", "/api/fact"):
        assert absent not in script, absent
    # A closed row opens no form, and the type is never patched.
    assert 'row.status === "pending"' in script
    assert '"type"' not in script.split("function editForm(")[1].split("\n}\n")[0]
    # A selector is built from the one batch the page holds, so it cannot offer another's row.
    options = script.split("function referenceOptionsFor(")[1].split("\n}\n")[0]
    assert "batchState.review.candidates" in options
    assert "/api/" not in options and "fetch" not in options


def test_a_markup_value_reaches_the_edit_form_as_data(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The form seeds an input's `value`, never markup; the batch response carries the raw string."""
    batch_id = _elena_batch(db_file, tmp_path, capsys)
    with _client(db_file) as client:
        body = client.get("/api/batch", params={"id": batch_id}).json()
        page = client.get(f"/?token={_TOKEN}", headers={TOKEN_HEADER: ""}).text
    assert _MARKUP_NAME in [row["candidate"].get("value") for row in body["review"]["candidates"]]
    # The document itself carries no recorded value, and its only way to place one is `textContent`
    # or an input's `value`, neither of which parses markup.
    assert _MARKUP_NAME not in page
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page


@pytest.mark.parametrize("verb", ["amend", "withdraw", "commit"])
def test_a_streamed_body_is_cut_at_the_ceiling_it_declares_no_length_for(db_file: Path, verb: str) -> None:
    """A streamed body carries no `Content-Length`, so the ceiling has to hold as it arrives."""

    def chunks() -> Iterator[bytes]:
        for _ in range(4):
            yield b"x" * (web_app.MAX_BATCH_ACTION_BYTES // 2 + 1)

    with _client(db_file) as client:
        response = client.post(
            f"/api/batch/{verb}",
            content=chunks(),
            headers={**_SAME_ORIGIN, "content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_stale_edit_refusal_reloads_the_batch_rather_than_keeping_the_form(db_file: Path, tmp_path: Path) -> None:
    """`batch_changed` is not a correction the open form can make: its digest is already obsolete."""
    outcome = _run_page(db_file, tmp_path, _STALE_EDIT_RELOADS)
    assert outcome["refetched"] == 1
    assert outcome["message"].startswith("Refused: batch_changed.")
    assert outcome["digest"] == "d1"  # the reload's own state, not the one that was refused
    assert outcome["stillOpen"] == "p1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_amending_discards_the_acceptances_made_before_it(db_file: Path, tmp_path: Path) -> None:
    """An acceptance is a decision about content the amendment has just changed."""
    outcome = _run_page(db_file, tmp_path, _AMEND_DISCARDS_ACCEPTANCES)
    assert outcome["accepted"] == 1
    assert outcome["after"] == 0
    assert "1 acceptance(s) discarded" in outcome["message"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_resolved_person_row_can_still_change_or_clear_its_match(db_file: Path, tmp_path: Path) -> None:
    """The projection drops `match_candidates` once a row is matched; the control must not."""
    outcome = _run_page(db_file, tmp_path, _RESOLVED_ROW_KEEPS_A_PICKER)
    assert outcome["options"] == ["", "person-1"]
    assert outcome["opensOn"] == "person-1"  # on what is recorded, not silently on "unresolved"
    assert outcome["patch"] == {"matched_person_id": None}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_pasted_person_id_travels_exactly_as_it_was_entered(db_file: Path, tmp_path: Path) -> None:
    """A person id is format-opaque and the matcher compares it exactly; trimming loses one."""
    outcome = _run_page(db_file, tmp_path, _PASTED_ID_IS_VERBATIM)
    assert outcome["patch"] == {"matched_person_id": "  spaced-restored-id  "}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_multiline_value_is_neither_flattened_nor_patched_by_an_unrelated_edit(db_file: Path, tmp_path: Path) -> None:
    """An `<input type="text">` drops CR and LF, which would read back as an edit nobody made."""
    outcome = _run_page(db_file, tmp_path, _MULTILINE_SURVIVES)
    assert outcome["tag"] == "textarea"
    assert outcome["kept"] is True
    assert outcome["patch"] == {"name": "Elena Marshe"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_shown_amend_command_survives_a_posix_shell(db_file: Path, tmp_path: Path) -> None:
    """The read-only `evidence_ids` fallback is advertised as a command, so it has to run."""
    command = _run_page(db_file, tmp_path, _SHELL_COMMAND)["command"]
    argv = shlex.split(command)
    assert argv[:3] == ["pctx", "import", "amend"]
    assert argv[3] == "B" and argv[4] == "cand'1"
    assert argv[5] == "--patch"
    assert json.loads(argv[6]) == {"evidence_ids": ["obs-1", "it's-opaque"]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_one_save_carries_both_the_match_and_the_field_edits(db_file: Path, tmp_path: Path) -> None:
    """The picker is a control of the form, so neither it nor the fields can drop the other."""
    outcome = _run_page(db_file, tmp_path, _ONE_SAVE_CARRIES_BOTH)
    assert outcome["both"] == {"summary": "Runs the allotment", "matched_person_id": "person-2"}
    # An untouched picker stays out: naming `matched_person_id` reopens identity in the use case,
    # which would put a choice the reviewer already made back to ambiguous.
    assert outcome["untouched"] == {"summary": "Runs the allotment club"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_accepting_rows_does_not_discard_an_open_form_s_unsaved_edits(db_file: Path, tmp_path: Path) -> None:
    """Accepting writes nothing, so it must not cost the reviewer what they have typed."""
    outcome = _run_page(db_file, tmp_path, _ACCEPT_KEEPS_THE_OPEN_FORM)
    assert outcome["kept"] == "Elena Marshe"
    assert outcome["enabled"] is True
    assert outcome["patch"] == {"name": "Elena Marshe"}


def _person_with_aliases(db_file: Path) -> str:
    conn = open_db(db_file)
    try:
        person = Person(
            canonical_name="Priya Sharma",
            aliases=[Alias(value="priya@example.com"), Alias(value="Pri", kind=AliasKind.NICKNAME, lang="en")],
        )
        SqlitePeopleRepository(conn).save_person(person)
        return person.id
    finally:
        conn.close()


def test_the_person_view_carries_alias_details_beside_the_brief(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        body = client.get("/api/person", params={"id": person_id}).json()

    assert body["format"] == "people-context-brief"
    assert body["person"]["aliases"] == ["priya@example.com", "Pri"]
    assert body["alias_details"] == [
        {"value": "priya@example.com", "kind": "other", "lang": None, "script": None},
        {"value": "Pri", "kind": "nickname", "lang": "en", "script": None},
    ]
    assert body["alias_kinds"] == [kind.value for kind in AliasKind]


def test_the_person_page_removes_and_corrects_aliases_through_the_use_cases(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        removed = client.post("/api/person/alias/remove", json={"person_id": person_id, "value": "PRIYA@example.com"})
        updated = client.post(
            "/api/person/alias/update", json={"person_id": person_id, "value": "pri", "new_value": "Priyu"}
        )
        after = client.get("/api/person", params={"id": person_id}).json()

    assert removed.status_code == 200
    assert removed.json() == {"person_id": person_id, "alias": "priya@example.com", "removed": True}
    assert updated.json()["changed"] == ["value"]
    assert [alias["value"] for alias in after["alias_details"]] == ["Priyu"]
    conn = open_db(db_file)
    try:
        sources = [row[0] for row in conn.execute("SELECT source FROM audit_log WHERE entity_id = ?", (person_id,))]
    finally:
        conn.close()
    assert sources.count("browser") == 2


@pytest.mark.parametrize(
    ("path", "body", "status", "expected"),
    [
        (
            "/api/person/alias/remove",
            {"value": "Priya Sharma"},
            409,
            {"error": "alias_conflict", "reason": "canonical_name"},
        ),
        (
            "/api/person/alias/update",
            {"value": "Pri", "new_value": "PRIYA SHARMA"},
            409,
            {"error": "alias_conflict", "reason": "duplicate_name"},
        ),
        ("/api/person/alias/update", {"value": "Nobody", "kind": "handle"}, 404, {"error": "alias_not_found"}),
        (
            "/api/person/alias/update",
            {"value": "Pri", "new_value": "pri@example.com"},
            409,
            {"error": "alias_conflict", "reason": "alias_email_not_handle"},
        ),
        ("/api/person/alias/update", {"value": "Pri", "kind": "OTHER"}, 400, {"error": "invalid_request"}),
        ("/api/person/alias/remove", {"value": "Pri", "extra": _SENTINEL}, 400, {"error": "invalid_request"}),
    ],
)
def test_alias_refusals_name_a_code_and_never_the_value(
    db_file: Path, path: str, body: dict[str, Any], status: int, expected: dict[str, str]
) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post(path, json={"person_id": person_id, **body})
    assert response.status_code == status
    assert response.json() == expected
    assert _SENTINEL not in response.text


def test_the_person_view_renames_and_rewrites_the_summary_through_edit_person(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        renamed = client.post("/api/person/edit", json={"person_id": person_id, "name": "Priya S. Sharma"})
        summarised = client.post("/api/person/edit", json={"person_id": person_id, "summary": "Runs the book club"})
        after = client.get("/api/person", params={"id": person_id}).json()
        cleared = client.post("/api/person/edit", json={"person_id": person_id, "summary": ""})
        emptied = client.get("/api/person", params={"id": person_id}).json()

    assert (renamed.status_code, summarised.status_code, cleared.status_code) == (200, 200, 200)
    assert after["person"]["canonical_name"] == "Priya S. Sharma"
    assert after["person"]["summary"] == "Runs the book club"
    # A rename keeps the name being replaced as an alias, exactly as `pctx edit` does.
    assert "Priya Sharma" in [alias["value"] for alias in after["alias_details"]]
    assert not emptied["person"]["summary"]
    conn = open_db(db_file)
    try:
        sources = [row[0] for row in conn.execute("SELECT source FROM audit_log WHERE entity_id = ?", (person_id,))]
    finally:
        conn.close()
    assert sources.count("browser") == 3


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        ({"name": "Elena Marsh"}, 409, {"error": "name_conflict"}),
        ({"name": "priya@example.org"}, 400, {"error": "name_is_email"}),
        ({"name": "   "}, 400, {"error": "invalid_request"}),
        ({}, 400, {"error": "invalid_request"}),
        ({"summary": "x", "extra": _SENTINEL}, 400, {"error": "invalid_request"}),
    ],
)
def test_person_edit_refusals_name_a_code_and_never_the_value(
    db_file: Path, body: dict[str, Any], status: int, expected: dict[str, str]
) -> None:
    _seed_people(db_file)
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post("/api/person/edit", json={"person_id": person_id, **body})
    assert response.status_code == status
    assert response.json() == expected
    assert _SENTINEL not in response.text


def test_a_person_edit_for_an_unknown_person_is_refused(db_file: Path) -> None:
    with _client(db_file) as client:
        response = client.post("/api/person/edit", json={"person_id": "missing", "summary": "x"})
    assert (response.status_code, response.json()) == (404, {"error": "unknown_person"})


def _affiliation_rows(db_file: Path) -> list[tuple[str, str, str | None, str | None, str]]:
    conn = open_db(db_file)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT o.name, f.role, f.valid_from, f.valid_to, f.provenance_source FROM affiliations f"
                " JOIN organizations o ON o.id = f.org_id ORDER BY f.created_at"
            )
        ]
    finally:
        conn.close()


def test_the_person_view_adds_an_affiliation_through_set_affiliation(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        first = client.post(
            "/api/person/affiliation/add",
            json={"person_id": person_id, "org": "Presight", "role": "engineer", "valid_from": "2026-09-01"},
        )
        # Another role at the same organization, named differently cased: one organization, two roles.
        second = client.post(
            "/api/person/affiliation/add", json={"person_id": person_id, "org": "PRESIGHT", "role": "lead"}
        )
        repeat = client.post(
            "/api/person/affiliation/add",
            json={"person_id": person_id, "org": "Presight", "role": "engineer", "valid_from": "2026-09-01"},
        )
        shown = client.get("/api/person", params={"id": person_id}).json()

    assert (first.status_code, second.status_code, repeat.status_code) == (200, 200, 200)
    assert repeat.json() == first.json()  # an exact repeat returns the stored row
    assert _affiliation_rows(db_file) == [
        ("Presight", "engineer", "2026-09-01", None, "browser"),
        ("Presight", "lead", None, None, "browser"),
    ]
    assert sorted(a["affiliation"]["role"] for a in shown["affiliations"]) == ["engineer", "lead"]


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        ({"org": "Presight", "role": "x", "valid_from": "2026-10-01", "valid_to": "2026-09-01"}, 400, "invalid_period"),
        ({"org": "Presight", "role": "  "}, 400, "invalid_request"),
        ({"org": "Presight", "role": "x", "valid_from": "last spring"}, 400, "invalid_request"),
        ({"org": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "role": "x"}, 404, "unknown_organization"),
        ({"org": "Presight", "role": "x", "extra": _SENTINEL}, 400, "invalid_request"),
    ],
)
def test_affiliation_refusals_name_a_code_and_create_nothing(
    db_file: Path, body: dict[str, Any], status: int, expected: str
) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post("/api/person/affiliation/add", json={"person_id": person_id, **body})
    assert (response.status_code, response.json()) == (status, {"error": expected})
    assert _SENTINEL not in response.text
    conn = open_db(db_file)
    try:
        # A refused period must not leave a new organization behind.
        assert conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0
    finally:
        conn.close()
    assert _affiliation_rows(db_file) == []


def test_an_affiliation_for_an_unknown_person_is_refused(db_file: Path) -> None:
    with _client(db_file) as client:
        response = client.post("/api/person/affiliation/add", json={"person_id": "missing", "org": "A", "role": "b"})
    assert (response.status_code, response.json()) == (404, {"error": "unknown_person"})


def test_an_alias_write_for_an_unknown_person_is_refused(db_file: Path) -> None:
    with _client(db_file) as client:
        response = client.post("/api/person/alias/remove", json={"person_id": "missing", "value": "x"})
    assert (response.status_code, response.json()) == (404, {"error": "unknown_person"})


def test_a_cross_origin_alias_removal_is_not_acted_on(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        refused = client.post(
            "/api/person/alias/remove",
            json={"person_id": person_id, "value": "Pri"},
            headers={"origin": "http://evil.example"},
        )
        untokened = client.post(
            "/api/person/alias/remove", json={"person_id": person_id, "value": "Pri"}, headers={TOKEN_HEADER: ""}
        )
        after = client.get("/api/person", params={"id": person_id}).json()
    assert (refused.status_code, untokened.status_code) == (403, 403)
    assert [alias["value"] for alias in after["alias_details"]] == ["priya@example.com", "Pri"]


# --- Issue 224: per-person review ---------------------------------------------------------------

# Two staged people, one already known; a fact for each; one row about no staged person.
_PER_PERSON_REVIEW = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  const texts = (node) => node.all().map((n) => n.textContent || n.text).filter((t) => typeof t === "string" && t);
  const row = (id, ordinal, candidate, extra) => Object.assign(
    { id, ordinal, status: "pending", source: "import/agent:https://linear.app/acme/issue/ENG-1", candidate },
    extra || {});
  setRows([
    row("p1", 1, { type: "person", name: "Elena Marsh", aliases: [{ value: "Lena", kind: "nickname" }] }, {
      possible_matches: [{ id: "x", canonical_name: "Elena", name_truncated: false }] }),
    row("f1", 2, { type: "fact", person_candidate_id: "p1", predicate: "role", value: "Beekeeper",
      stated_by: "Jordan" }),
    row("p2", 3, { type: "person", name: "Sam Hale", matched_person_id: "person-9", match_disposition: "matched",
      match_count: 1 }),
    row("f2", 4, { type: "fact", person_candidate_id: "p2", predicate: "link", value: "javascript:alert(1)" }),
    row("g1", 5, { type: "group", name: "Allotment club", kind: "club" }),
  ]);
  await showBatch("B");
  const sections = view.all().filter((n) => n.tag === "section");
  const groups = sections.map((s) => [s.attrs["aria-label"], s.all().filter((n) => n.tag === "td"
    && /^#[0-9]+$/.test(n.children[0] && n.children[0].text)).map((n) => n.children[0].text)]);
  const badges = sections.map((s) => s.children[0].children.map((n) => n.textContent).filter(Boolean));
  const links = view.all().filter((n) => n.tag === "a").map((n) => [n.textContent, n.rel, n.target]);
  const elena = texts(sections[0]);
  // A validation refusal keeps the reviewer's typing in the open form.
  find("Edit").listeners.click();
  labelled("name").value = "Elena Marsh-Ibarra";
  failNextPost(400, { error: "invalid_amendment", fields: [{ field: "name", message: "too long" }] });
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  const kept = labelled("name").value;
  find("Close").listeners.click(); await tick();
  // Rejecting one row withdraws that row only.
  labelled("Reject #2").listeners.click();
  releases.forEach((r) => r()); await tick();
  // Accepting a person's rows accepts that group only, and the confirmation names them.
  find("Accept 2 for Sam Hale").listeners.click(); await tick();
  find("Commit accepted").listeners.click();
  const confirm = view.all().find((n) => n.tag === "ul");
  console.log(JSON.stringify({ groups, badges, links, elena, kept,
    withdraw: JSON.parse(posts[1].body).candidate_ids, accepted: Array.from(batchState.accepted),
    confirm: texts(confirm) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_batch_is_reviewed_person_by_person_with_readable_proposals(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _PER_PERSON_REVIEW)
    assert outcome["groups"] == [
        ["Elena Marsh", ["#1", "#2"]],
        ["Sam Hale", ["#3", "#4"]],
        ["Not about one staged person", ["#5"]],
    ]
    assert outcome["badges"][:2] == [["New person"], ["Existing person"]]
    # One source for the batch, shown once with its address linked; another scheme stays text.
    assert outcome["links"] == [["https://linear.app/acme/issue/ENG-1", "noopener noreferrer", "_blank"]]
    assert "javascript:alert(1)" not in [text for text, _rel, _target in outcome["links"]]
    assert "Lena (nickname)" in outcome["elena"]
    assert "Jordan" in outcome["elena"] and "stated by" in outcome["elena"]
    assert any(text.startswith("Possibly the same as Elena.") for text in outcome["elena"])
    assert outcome["kept"] == "Elena Marsh-Ibarra"
    assert outcome["withdraw"] == ["f1"]
    assert sorted(outcome["accepted"]) == ["f2", "p2"]
    assert outcome["confirm"] == ["#3 person: Sam Hale", "#4 fact: link = javascript:alert(1)"]


def test_a_seven_person_import_is_reviewed_and_committed_in_the_browser(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names = ["Ada Byrne", "Ben Okafor", "Cleo Ruiz", "Dev Patel", "Eve Lund", "Finn Moss", "Gus Hart"]
    candidates: list[Any] = []
    for index, name in enumerate(names):
        candidates.append({"type": "person", "ref": f"p{index}", "name": name, "aliases": []})
        candidates.append({"type": "fact", "person_ref": f"p{index}", "predicate": f"role{index}", "value": "Engineer"})
    batch_id = _stage(db_file, tmp_path, capsys, candidates)
    with _client(db_file) as client:
        shown = client.get("/api/batch", params={"id": batch_id}).json()["review"]
        ids = _ids(shown)
        amended = _amend(client, batch_id, ids["Cleo Ruiz"], {"name": "Cleo Ruiz-Ortega"}, shown["batch_digest"])
        assert amended.status_code == 200
        digest = amended.json()["batch_digest"]
        withdrawn = _action(client, "withdraw", batch_id, [ids["role3"]], digest)
        assert withdrawn.status_code == 200
        accept = [row_id for key, row_id in ids.items() if key != "role3"]
        # A review taken before the amendment no longer matches the batch and is refused.
        stale = _action(client, "commit", batch_id, accept, digest)
        assert stale.status_code == 409 and stale.json() == {"error": "batch_changed"}
        committed = _action(client, "commit", batch_id, accept, withdrawn.json()["batch_digest"])
    assert committed.status_code == 200
    assert sorted(committed.json()["committed_ids"]) == sorted(accept)
    final = {row["id"]: row for row in _cli_review(capsys, db_file, batch_id)["candidates"]}
    assert final[ids["role3"]]["status"] == "rejected"
    assert final[ids["Cleo Ruiz"]]["candidate"]["name"] == "Cleo Ruiz-Ortega"


def test_the_person_page_adds_an_alias_through_the_use_case(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        added = client.post(
            "/api/person/alias/add",
            json={"person_id": person_id, "value": "Priyanka", "kind": "former_name", "lang": "en"},
        )
        again = client.post("/api/person/alias/add", json={"person_id": person_id, "value": "PRIYANKA"})
        email = client.post(
            "/api/person/alias/add", json={"person_id": person_id, "value": "p.sharma@example.com", "kind": "handle"}
        )
        after = client.get("/api/person", params={"id": person_id}).json()
    assert added.json() == {"person_id": person_id, "alias": "Priyanka", "added": True}
    # The same name again, in any case, changes nothing and says so.
    assert again.json() == {"person_id": person_id, "alias": "PRIYANKA", "added": False}
    assert email.json()["added"] is True
    assert {"value": "Priyanka", "kind": "former_name", "lang": "en", "script": None} in after["alias_details"]
    assert "p.sharma@example.com" in after["person"]["emails"]
    conn = open_db(db_file)
    try:
        sources = [row[0] for row in conn.execute("SELECT source FROM audit_log WHERE entity_id = ?", (person_id,))]
    finally:
        conn.close()
    assert sources.count("browser") == 3


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        ({"value": _SENTINEL + "@example.com"}, 409, {"error": "alias_conflict", "reason": "alias_email_not_handle"}),
        ({"value": "Pri", "kind": "OTHER"}, 400, {"error": "invalid_request"}),
        ({"value": "   "}, 400, {"error": "invalid_request"}),
        ({"value": "Pri", "extra": _SENTINEL}, 400, {"error": "invalid_request"}),
    ],
)
def test_alias_add_refusals_name_a_code_and_never_the_value(
    db_file: Path, body: dict[str, Any], status: int, expected: dict[str, str]
) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post("/api/person/alias/add", json={"person_id": person_id, **body})
        unknown = client.post("/api/person/alias/add", json={"person_id": "missing", "value": "x"})
    assert (response.status_code, response.json()) == (status, expected)
    assert _SENTINEL not in response.text
    assert (unknown.status_code, unknown.json()) == (404, {"error": "unknown_person"})


_ADD_ALIAS = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, affiliations: [], facts: [], interactions: [], reminders: [],
    traits: [], alias_kinds: ["nickname", "handle", "other"], alias_details: [] };
  await showPerson("P", null, []);
  find("Add alias").listeners.click();
  find("Add").listeners.click();
  const empty = posts.length;
  labelled("new alias value").value = "Jordy";
  find("Add").listeners.click();
  releases.forEach((r) => r()); await tick();
  await showPerson("P", null, []);
  find("Add email").listeners.click();
  labelled("new alias email address").value = "jordan@example.com";
  find("Add").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ empty, posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_add_buttons_post_a_nickname_and_an_email_handle(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _ADD_ALIAS)
    assert outcome == {
        "empty": 0,
        "posts": [
            ["/api/person/alias/add", {"person_id": "P", "value": "Jordy", "kind": "nickname"}],
            ["/api/person/alias/add", {"person_id": "P", "value": "jordan@example.com", "kind": "handle"}],
        ],
    }


# --- Correcting stored records from the person view -------------------------------------------


def _person_with_records(db_file: Path) -> dict[str, str]:
    """Priya with an ordinary fact, a sensitive fact, and an affiliation; returns their ids."""
    from people_context.app.records import SetAffiliationInput

    person_id = _person_with_aliases(db_file)
    runtime = build_runtime(db_file)
    try:
        cases = runtime.use_cases
        fact = cases.record_fact.execute(RecordFactInput(person_id=person_id, predicate="role", value="Gardener"))
        hidden = cases.record_fact.execute(
            RecordFactInput(
                person_id=person_id, predicate="health", value=_SENSITIVE_VALUE, sensitivity=Sensitivity.SENSITIVE
            )
        )
        affiliation = cases.set_affiliation.execute(SetAffiliationInput(person_id=person_id, org="Acme", role="Eng"))
        interaction = cases.record_interaction.execute(
            RecordInteractionInput(summary="Seed swap", participant_ids=[person_id], channel="garden")
        )
    finally:
        runtime.close()
    return {
        "person": person_id,
        "fact": fact.id,
        "hidden": hidden.id,
        "affiliation": affiliation.id,
        "interaction": interaction.id,
    }


def _correct(client: TestClient, entity_type: str, entity_id: str, fields: dict[str, Any]) -> Any:
    return client.post(
        "/api/record/correct", json={"entity_type": entity_type, "entity_id": entity_id, "fields": fields}
    )


def test_the_person_page_corrects_facts_and_affiliations_in_place(db_file: Path) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        fact = _correct(client, "fact", ids["fact"], {"value": "Head gardener", "valid_from": "2024-03-01"})
        affiliation = _correct(client, "affiliation", ids["affiliation"], {"role": "Engineer"})
        after = client.get("/api/person", params={"id": ids["person"]}).json()
    assert fact.json() == {"corrected": ["valid_from", "value"]}
    assert affiliation.json() == {"corrected": ["role"]}
    assert "Head gardener" not in fact.text
    stored = next(row for row in after["facts"] if row["id"] == ids["fact"])
    assert (stored["value"], stored["period"]["valid_from"]) == ("Head gardener", "2024-03-01")
    assert after["affiliations"][0]["affiliation"]["role"] == "Engineer"
    assert set(after["record_fields"]) == {"affiliation", "fact", "interaction", "trait", "reminder"}
    conn = open_db(db_file)
    try:
        ops = conn.execute("SELECT entity_id FROM audit_log WHERE op = 'correct' AND source = 'browser'").fetchall()
    finally:
        conn.close()
    assert sorted(row[0] for row in ops) == sorted([ids["fact"], ids["affiliation"]])


@pytest.mark.parametrize(
    ("entity_type", "target", "fields", "status", "expected"),
    [
        # A record the view's disclosure hides is as unknown as one that does not exist.
        ("fact", "hidden", {"value": _SENTINEL}, 404, {"error": "record_not_found"}),
        ("fact", "missing", {"value": _SENTINEL}, 404, {"error": "record_not_found"}),
        ("fact", "fact", {"sensitivity": _SENTINEL}, 400, {"error": "invalid_correction", "fields": ["sensitivity"]}),
        # An undeclared field is the caller's own text, so it is not named back.
        ("fact", "fact", {_SENTINEL: "x"}, 400, {"error": "invalid_correction", "fields": []}),
        ("affiliation", "affiliation", {"org_id": _SENTINEL}, 400, {"error": "invalid_correction", "fields": []}),
        ("observation", "fact", {"text": _SENTINEL}, 400, {"error": "invalid_request"}),
        # A correction names its record's own type; a fact's id is no interaction.
        ("interaction", "fact", {"summary": _SENTINEL}, 404, {"error": "record_not_found"}),
    ],
)
def test_record_corrections_are_refused_by_code_without_echoing_the_value(
    db_file: Path, entity_type: str, target: str, fields: dict[str, Any], status: int, expected: dict[str, Any]
) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        response = _correct(client, entity_type, ids.get(target, target), fields)
        after = client.get("/api/person", params={"id": ids["person"]}).json()
    assert (response.status_code, response.json()) == (status, expected)
    assert _SENTINEL not in response.text and _SENSITIVE_VALUE not in response.text
    assert next(row for row in after["facts"] if row["id"] == ids["fact"])["value"] == "Gardener"


_EDIT_A_FACT = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, affiliations: [], interactions: [], reminders: [], traits: [],
    alias_kinds: [], alias_details: [], record_fields: __RECORD_FIELDS__,
    facts: [{ id: "F1", predicate: "role", value: "Gardener", confidence: 0.9, sensitivity: "personal",
      period: { valid_from: "2024-01-01", valid_to: null } }] };
  await showPerson("P", null, []);
  labelled("Edit role: Gardener").listeners.click();
  find("Save").listeners.click();
  const unchanged = posts.length;
  labelled("Edit role: Gardener").listeners.click();
  labelled("value").value = "Head gardener";
  labelled("valid_from").value = "";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ unchanged, posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


_EDIT_SUMMARY = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", aliases: [], emails: [],
      summary: "Neighbour" },
    disclosure: { context: "ordinary" }, affiliations: [], facts: [], interactions: [], reminders: [], traits: [],
    alias_kinds: [], alias_details: [], record_fields: {} };
  await showPerson("P", null, []);
  labelled("Edit summary").listeners.click();
  find("Save").listeners.click();
  const unchanged = posts.length;
  labelled("Edit summary").listeners.click();
  labelled("summary").value = "";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ unchanged, posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_summary_edit_sends_only_a_moved_value(db_file: Path, tmp_path: Path) -> None:
    outcome = _run_page(db_file, tmp_path, _EDIT_SUMMARY)
    assert outcome == {
        "unchanged": 0,
        "posts": [["/api/person/edit", {"person_id": "P", "summary": ""}]],
    }


_ADD_AN_AFFILIATION = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, affiliations: [], facts: [], interactions: [], reminders: [], traits: [],
    alias_kinds: [], alias_details: [], record_fields: {} };
  await showPerson("P", null, []);
  find("Add affiliation").listeners.click();
  labelled("new affiliation organization").value = "Presight";
  find("Add").listeners.click();
  const blocked = posts.length;
  labelled("new affiliation role").value = " engineer ";
  labelled("new affiliation from").value = "2026-09-01";
  find("Add").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ blocked, posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_affiliation_form_sends_trimmed_values_and_only_the_dates_given(db_file: Path, tmp_path: Path) -> None:
    assert _run_page(db_file, tmp_path, _ADD_AN_AFFILIATION) == {
        "blocked": 0,
        "posts": [
            [
                "/api/person/affiliation/add",
                {"person_id": "P", "org": "Presight", "role": "engineer", "valid_from": "2026-09-01"},
            ]
        ],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_editing_a_fact_sends_only_the_fields_that_moved(db_file: Path, tmp_path: Path) -> None:
    from people_context.adapters.web.fields import RECORD_FIELDS

    outcome = _run_page(db_file, tmp_path, _EDIT_A_FACT.replace("__RECORD_FIELDS__", json.dumps(RECORD_FIELDS)))
    assert outcome == {
        "unchanged": 0,
        "posts": [
            [
                "/api/record/correct",
                {"entity_type": "fact", "entity_id": "F1", "fields": {"value": "Head gardener", "valid_from": None}},
            ]
        ],
    }


# --- Adding and removing facts and interactions, and removing affiliations, from the person view --


def _browser_ops(db_file: Path, op: str) -> list[tuple[str, str]]:
    conn = open_db(db_file)
    try:
        rows = conn.execute(
            "SELECT entity_type, entity_id FROM audit_log WHERE op = ? AND source = 'browser' ORDER BY rowid", (op,)
        ).fetchall()
    finally:
        conn.close()
    return [(row[0], row[1]) for row in rows]


def test_the_person_view_adds_a_fact_through_record_fact(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        added = client.post(
            "/api/person/fact/add",
            json={"person_id": person_id, "predicate": "lives_in", "value": "Dubai", "valid_from": "2024-01-01"},
        )
        shown = client.get("/api/person", params={"id": person_id}).json()
    assert added.status_code == 200
    fact_id = added.json()["fact_id"]
    stored = next(row for row in shown["facts"] if row["id"] == fact_id)
    assert (stored["predicate"], stored["value"], stored["period"]["valid_from"]) == ("lives_in", "Dubai", "2024-01-01")
    assert (stored["sensitivity"], stored["provenance"]["source"]) == ("personal", "browser")
    assert _browser_ops(db_file, "create") == [("fact", fact_id)]


@pytest.mark.parametrize(
    ("path", "body", "status", "expected"),
    [
        (
            "fact",
            {"predicate": "x", "value": "y", "valid_from": "2026-10-01", "valid_to": "2026-09-01"},
            400,
            "invalid_period",
        ),
        ("fact", {"predicate": " ", "value": _SENTINEL}, 400, "invalid_request"),
        ("fact", {"predicate": "x", "value": "y", "sensitivity": "sensitive"}, 400, "invalid_request"),
        ("interaction", {"summary": "  "}, 400, "invalid_request"),
        ("interaction", {"summary": _SENTINEL, "occurred_on": "yesterday"}, 400, "invalid_request"),
        ("interaction", {"summary": "x", "participant_ids": [_SENTINEL]}, 400, "invalid_request"),
    ],
)
def test_record_additions_are_refused_by_code_and_write_nothing(
    db_file: Path, path: str, body: dict[str, Any], status: int, expected: str
) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post(f"/api/person/{path}/add", json={"person_id": person_id, **body})
        unknown = client.post(
            f"/api/person/{path}/add",
            json={"person_id": "missing", "predicate": "x", "value": "y"}
            if path == "fact"
            else {"person_id": "missing", "summary": "x"},
        )
    assert (response.status_code, response.json()) == (status, {"error": expected})
    assert _SENTINEL not in response.text
    assert (unknown.status_code, unknown.json()) == (404, {"error": "unknown_person"})
    assert _browser_ops(db_file, "create") == []


def test_the_person_view_adds_an_interaction_with_that_person(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        dated = client.post(
            "/api/person/interaction/add",
            json={"person_id": person_id, "summary": "Lunch", "channel": "in person", "occurred_on": "2026-08-30"},
        )
        undated = client.post("/api/person/interaction/add", json={"person_id": person_id, "summary": "Call"})
        shown = client.get("/api/person", params={"id": person_id}).json()
    assert (dated.status_code, undated.status_code) == (200, 200)
    by_id = {row["id"]: row for row in shown["interactions"]}
    lunch = by_id[dated.json()["interaction_id"]]
    assert (lunch["summary"], lunch["channel"], lunch["participant_ids"]) == ("Lunch", "in person", [person_id])
    assert datetime.fromisoformat(lunch["occurred_at"]) == datetime(2026, 8, 30, tzinfo=UTC)
    assert by_id[undated.json()["interaction_id"]]["channel"] is None
    assert [entity for entity, _ in _browser_ops(db_file, "create")] == ["interaction", "interaction"]


def test_the_person_view_corrects_an_interaction_in_place(db_file: Path) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        corrected = _correct(client, "interaction", ids["interaction"], {"summary": "Seed and bulb swap"})
        refused = _correct(client, "interaction", ids["interaction"], {"participant_ids": [_SENTINEL]})
        shown = client.get("/api/person", params={"id": ids["person"]}).json()
    assert corrected.json() == {"corrected": ["summary"]}
    assert (refused.status_code, refused.json()) == (400, {"error": "invalid_correction", "fields": []})
    assert shown["interactions"][0]["summary"] == "Seed and bulb swap"
    assert _browser_ops(db_file, "correct") == [("interaction", ids["interaction"])]


def test_the_person_view_removes_affiliations_facts_and_interactions_through_forget(db_file: Path) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        removed = [
            client.post("/api/record/remove", json={"entity_type": kind, "entity_id": ids[kind]})
            for kind in ("affiliation", "fact", "interaction")
        ]
        again = client.post("/api/record/remove", json={"entity_type": "fact", "entity_id": ids["fact"]})
        shown = client.get("/api/person", params={"id": ids["person"]}).json()
    assert [response.json() for response in removed] == [
        {"removed": "affiliation"},
        {"removed": "fact"},
        {"removed": "interaction"},
    ]
    assert (again.status_code, again.json()) == (404, {"error": "record_not_found"})
    assert (shown["affiliations"], shown["facts"], shown["interactions"]) == ([], [], [])
    assert [entity for entity, _ in _browser_ops(db_file, "forget")] == ["record"] * 3
    conn = open_db(db_file)
    try:
        counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("affiliations", "interactions")]
        # The hidden fact was never on the page and is untouched.
        facts = [row[0] for row in conn.execute("SELECT id FROM facts")]
    finally:
        conn.close()
    assert counts == [0, 0]
    assert facts == [ids["hidden"]]


@pytest.mark.parametrize(
    ("entity_type", "target", "status", "expected"),
    [
        # A record the view's disclosure hides is as unknown as one that does not exist.
        ("fact", "hidden", 404, "record_not_found"),
        ("fact", "missing", 404, "record_not_found"),
        ("interaction", "fact", 404, "record_not_found"),
        # Only the types the page offers Remove for; anything else is not a page request.
        ("trait", "fact", 400, "invalid_request"),
        ("person", "person", 400, "invalid_request"),
    ],
)
def test_record_removals_are_refused_by_code_and_delete_nothing(
    db_file: Path, entity_type: str, target: str, status: int, expected: str
) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        response = client.post(
            "/api/record/remove", json={"entity_type": entity_type, "entity_id": ids.get(target, target)}
        )
        shown = client.get("/api/person", params={"id": ids["person"]}).json()
    assert (response.status_code, response.json()) == (status, {"error": expected})
    assert _SENSITIVE_VALUE not in response.text
    assert _browser_ops(db_file, "forget") == []
    assert len(shown["facts"]) == 1 and len(shown["interactions"]) == 1


def test_a_cross_origin_removal_is_not_acted_on(db_file: Path) -> None:
    ids = _person_with_records(db_file)
    with _client(db_file) as client:
        refused = client.post(
            "/api/record/remove",
            json={"entity_type": "fact", "entity_id": ids["fact"]},
            headers={"origin": "http://evil.example"},
        )
    assert refused.status_code == 403
    assert _browser_ops(db_file, "forget") == []


_ADD_AND_REMOVE_RECORDS = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  const texts = () => view.all().map((n) => n.textContent || n.text).filter((t) => typeof t === "string" && t);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, reminders: [], traits: [], alias_kinds: [], alias_details: [],
    record_fields: __RECORD_FIELDS__,
    affiliations: [{ affiliation: { id: "A1", role: "Eng", period: {} }, organization_name: "Acme" }],
    facts: [{ id: "F1", predicate: "role", value: "Gardener", period: {} }],
    interactions: [{ id: "I1", summary: "Lunch", occurred_at: "2026-08-30T00:00:00Z", channel: "in person",
      participant_ids: ["P", "Q"] }] };
  await showPerson("P", null, []);
  const reminderHasRemove = !!labelled("Remove " + "reminder");
  // Remove asks first; Cancel sends nothing.
  labelled("Remove role: Gardener").listeners.click();
  const factPrompt = texts().find((t) => t.startsWith("Permanently remove"));
  find("Cancel").listeners.click();
  const cancelled = posts.length;
  labelled("Remove 2026-08-30 (in person): Lunch").listeners.click();
  const sharedPrompt = texts().find((t) => t.startsWith("Permanently remove"));
  labelled("Confirm removal of 2026-08-30 (in person): Lunch").listeners.click();
  releases.forEach((r) => r()); await tick();
  await showPerson("P", null, []);
  labelled("Remove Eng at Acme").listeners.click();
  labelled("Confirm removal of Eng at Acme").listeners.click();
  releases.forEach((r) => r()); await tick();
  await showPerson("P", null, []);
  find("Add fact").listeners.click();
  labelled("new fact predicate").value = "lives_in";
  find("Add").listeners.click();
  const blocked = posts.length;
  labelled("new fact value").value = " Dubai ";
  find("Add").listeners.click();
  releases.forEach((r) => r()); await tick();
  await showPerson("P", null, []);
  find("Add interaction").listeners.click();
  labelled("new interaction summary").value = "Call about seeds";
  labelled("new interaction on").value = "2026-09-01";
  find("Add").listeners.click();
  releases.forEach((r) => r()); await tick();
  await showPerson("P", null, []);
  labelled("Edit 2026-08-30 (in person): Lunch").listeners.click();
  labelled("summary").value = "Long lunch";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ reminderHasRemove, factPrompt, cancelled, sharedPrompt, blocked,
    posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_person_view_adds_edits_and_removes_after_confirmation(db_file: Path, tmp_path: Path) -> None:
    from people_context.adapters.web.fields import RECORD_FIELDS

    outcome = _run_page(
        db_file, tmp_path, _ADD_AND_REMOVE_RECORDS.replace("__RECORD_FIELDS__", json.dumps(RECORD_FIELDS))
    )
    assert outcome["reminderHasRemove"] is False
    assert outcome["factPrompt"] == "Permanently remove role: Gardener? This cannot be undone."
    assert outcome["cancelled"] == 0
    assert outcome["sharedPrompt"].endswith("It is removed for every participant.")
    assert outcome["blocked"] == 2
    assert outcome["posts"] == [
        ["/api/record/remove", {"entity_type": "interaction", "entity_id": "I1"}],
        ["/api/record/remove", {"entity_type": "affiliation", "entity_id": "A1"}],
        ["/api/person/fact/add", {"person_id": "P", "predicate": "lives_in", "value": "Dubai"}],
        ["/api/person/interaction/add", {"person_id": "P", "summary": "Call about seeds", "occurred_on": "2026-09-01"}],
        ["/api/record/correct", {"entity_type": "interaction", "entity_id": "I1", "fields": {"summary": "Long lunch"}}],
    ]


def test_explicit_person_names_can_be_edited_and_cleared_in_browse(db_file: Path) -> None:
    person_id = _person_with_aliases(db_file)
    with _client(db_file) as client:
        response = client.post(
            "/api/person/edit",
            json={
                "person_id": person_id,
                "preferred_name": "Pri",
                "given_name": "Priya",
                "family_name": "Sharma",
                "family_name_first": True,
            },
        )
        assert response.status_code == 200
        person = client.get("/api/person", params={"id": person_id}).json()["person"]
        assert person["display_name"] == "Pri"
        assert person["canonical_name"] == "Priya Sharma"
        assert person["full_name"] == "Sharma Priya"
        assert person["family_name_first"] is True
        assert client.post("/api/person/edit", json={"person_id": person_id, "preferred_name": ""}).status_code == 200
        person = client.get("/api/person", params={"id": person_id}).json()["person"]
        assert person["preferred_name"] is None
        assert person["display_name"] == "Priya"
        assert person["family_name"] == "Sharma"
        assert client.post("/api/person/edit", json={"person_id": person_id, "given_name": "p@example.org"}).json() == {
            "error": "name_is_email",
        }


_EDIT_PREFERRED_NAME = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (label) => view.all().find((n) => (n.attrs || {})["aria-label"] === label);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan Lee", display_name: "Jordy",
    preferred_name: "Jordy", given_name: "Jordan", family_name: "Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, affiliations: [], facts: [], interactions: [], reminders: [], traits: [],
    alias_kinds: [], alias_details: [], record_fields: {} };
  await showPerson("P", null, []);
  const title = view.children[0].textContent;
  labelled("Edit preferred name").listeners.click();
  labelled("preferred_name").value = "";
  find("Save").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ title, posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_preferred_name_heading_and_clear_action(db_file: Path, tmp_path: Path) -> None:
    result = _run_page(db_file, tmp_path, _EDIT_PREFERRED_NAME)
    assert result == {
        "title": "Jordy",
        "posts": [["/api/person/edit", {"person_id": "P", "preferred_name": ""}]],
    }


_NAME_DETAILS = r"""
(async () => {
  globalThis.personDoc = { person: { id: "P", canonical_name: "Wang Xiaoming", full_name: "王小明",
    display_name: "小明", preferred_name: "小明", given_name: "小明", family_name: "王", family_name_first: true,
    aliases: ["Xiaoming"], emails: [] }, disclosure: { context: "ordinary" }, affiliations: [], facts: [],
    interactions: [], reminders: [], traits: [], alias_kinds: ["transliteration"],
    alias_details: [{value: "Xiaoming", kind: "transliteration"}], record_fields: {} };
  await showPerson("P", null, []);
  const identity = view.children.find((n) => n.tag === "dl");
  const details = view.children.find((n) => n.tag === "details");
  if (!details) { console.log(JSON.stringify({ details: false })); return; }
  const terms = identity.children.filter((n) => n.tag === "dt").map((n) => n.textContent);
  const values = identity.children.filter((n) => n.tag === "dd");
  const order = details.all().find((n) => n.type === "checkbox");
  const state = { terms, full: values[1].textContent, collapsed: !details.open,
    summary: details.children[0].textContent, order: order.checked,
    nameEditors: identity.all().filter((n) => n.tag === "button").length,
    parts: details.all().filter((n) => n.tag === "dt").map((n) => n.textContent) };
  details.open = true;
  details.all().find((n) => n.tag === "button"
    && n.attrs["aria-label"] === t("person.edit_given_name")).listeners.click();
  details.all().find((n) => n.attrs && n.attrs["aria-label"] === fieldAria("given_name")).value = "小亮";
  details.all().find((n) => n.tag === "button" && n.textContent === t("common.save")).listeners.click();
  releases.forEach((r) => r()); await tick();
  const openAfterSave = Boolean(view.children.find((n) => n.tag === "details").open);
  document.getElementById("nav-lang").listeners.click(); await tick();
  const refreshed = view.children.find((n) => n.tag === "details");
  const openAfterLanguage = Boolean(refreshed.open);
  const changedOrder = refreshed.all().find((n) => n.type === "checkbox");
  holdGets = true;
  changedOrder.checked = false; changedOrder.listeners.change();
  releases.forEach((r) => r()); await tick();
  refreshed.open = false;
  holdGets = false; heldGets.forEach((r) => r()); await tick();
  const closedAfterSave = !view.children.find((n) => n.tag === "details").open;
  console.log(JSON.stringify({ ...state, openAfterSave, openAfterLanguage, closedAfterSave,
    posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    ("language", "terms", "summary", "parts"),
    [
        ("en", ["Preferred name", "Full name"], "Name details", ["Given name", "Family name"]),
        ("zh-CN", ["称呼名", "完整姓名"], "姓名详情", ["名", "姓"]),
    ],
)
def test_name_details_keep_full_name_read_only_and_edit_order(
    db_file: Path, tmp_path: Path, language: str, terms: list[str], summary: str, parts: list[str]
) -> None:
    result = _run_page(db_file, tmp_path, _NAME_DETAILS, language=language)
    assert result == {
        "terms": terms,
        "full": "王小明",
        "collapsed": True,
        "summary": summary,
        "order": True,
        "nameEditors": 1,
        "parts": parts,
        "openAfterSave": True,
        "openAfterLanguage": True,
        "closedAfterSave": True,
        "posts": [
            ["/api/person/edit", {"person_id": "P", "given_name": "小亮"}],
            ["/api/person/edit", {"person_id": "P", "family_name_first": False}],
        ],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_reviewed_boolean_control_submits_false_as_boolean(db_file: Path, tmp_path: Path) -> None:
    scenario = r"""
    (() => {
      const control = buildControl({name: "family_name_first", control: "boolean"},
        {candidate: {family_name_first: true}}, {});
      const untouched = control.read() === undefined;
      control.node.value = "false";
      console.log(JSON.stringify({untouched, submitted: control.read()}));
    })();
    """
    assert _run_page(db_file, tmp_path, scenario) == {"untouched": True, "submitted": False}


_CONCURRENT_NAME_EDITS = r"""
(async () => {
  globalThis.personDoc = { person: { id: "P", canonical_name: "王小明", full_name: "王小明",
    display_name: "小明", preferred_name: "小明", given_name: "小明", family_name: "王", family_name_first: true,
    aliases: [], emails: [] }, disclosure: { context: "ordinary" }, affiliations: [], facts: [],
    interactions: [], reminders: [], traits: [], alias_kinds: [], alias_details: [], record_fields: {} };
  const fields = __FIELDS_TO_EDIT__;
  const drafts = {given_name: "小亮", family_name: "张"};
  const input = (field) => view.all().find((n) => n.attrs && n.attrs["aria-label"] === fieldAria(field));
  const row = (field) => view.all().find((n) => n.tag === "div" && n.children.includes(input(field)));
  const action = (field, key) => row(field).children.find(
    (n) => n.tag === "button" && n.textContent === t(key)).listeners.click();
  await showPerson("P", null, []);
  view.children.find((n) => n.tag === "details").open = true;
  for (const field of fields) {
    view.all().find((n) => n.attrs && n.attrs["aria-label"] === t("person.edit_" + field)).listeners.click();
    input(field).value = drafts[field];
  }
  action(fields[0], "common.save");
  personDoc.person[fields[0]] = drafts[fields[0]];
  releases.forEach((r) => r()); await tick();
  const keptAfterSave = input(fields[1]) ? input(fields[1]).value : null;
  document.getElementById("nav-lang").listeners.click(); await tick();
  const keptAfterLanguage = input(fields[1]) ? input(fields[1]).value : null;
  if (!input(fields[1])) {
    console.log(JSON.stringify({keptAfterSave, keptAfterLanguage})); return;
  }
  action(fields[1], __SECOND_ACTION__);
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({keptAfterSave, keptAfterLanguage,
    posts: posts.map((p) => [p.path, JSON.parse(p.body)]) }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("fields", [["given_name", "family_name"], ["family_name", "given_name"]])
def test_saving_either_name_keeps_the_other_draft(
    db_file: Path, tmp_path: Path, language: str, fields: list[str]
) -> None:
    scenario = _CONCURRENT_NAME_EDITS.replace("__FIELDS_TO_EDIT__", json.dumps(fields)).replace(
        "__SECOND_ACTION__", json.dumps("common.save")
    )
    drafts = {"given_name": "小亮", "family_name": "张"}
    assert _run_page(db_file, tmp_path, scenario, language=language) == {
        "keptAfterSave": drafts[fields[1]],
        "keptAfterLanguage": drafts[fields[1]],
        "posts": [["/api/person/edit", {"person_id": "P", field: drafts[field]}] for field in fields],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_preserved_name_draft_can_be_cancelled(db_file: Path, tmp_path: Path) -> None:
    scenario = _CONCURRENT_NAME_EDITS.replace("__FIELDS_TO_EDIT__", json.dumps(["given_name", "family_name"])).replace(
        "__SECOND_ACTION__", json.dumps("common.cancel")
    )
    assert _run_page(db_file, tmp_path, scenario) == {
        "keptAfterSave": "张",
        "keptAfterLanguage": "张",
        "posts": [["/api/person/edit", {"person_id": "P", "given_name": "小亮"}]],
    }
