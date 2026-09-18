"""The `pctx browse` application: its guard, its security headers, and its read endpoints (M30.1)."""

from __future__ import annotations

import json
import re
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
from people_context.app.exports.brief import BRIEF_CONTEXT_ITEMS
from people_context.app.records import RecordFact, RecordFactInput
from people_context.cli.imports import REVIEW_DISCLOSURE_WARNING
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
