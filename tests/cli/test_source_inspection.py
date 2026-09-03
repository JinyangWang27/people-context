"""`pctx sources` and `pctx source show` end to end, against a real staged and committed import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db
from people_context.app.exports import SYNC_BUNDLE_FILENAME
from people_context.app.imports import (
    IMPORT_SOURCE_FORMAT,
    IMPORT_SOURCE_VERSION,
    IMPORT_SOURCES_FORMAT,
    IMPORT_SOURCES_VERSION,
    MAX_SOURCE_PAGE_LIMIT,
)

_LINKEDIN_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_URL = "https://example.invalid/in/sr"


def _linkedin(tmp_path: Path, name: str = "connections.csv", rows: int = 2) -> Path:
    source = tmp_path / name
    source.write_text(
        "\n".join(
            [_LINKEDIN_HEADERS]
            + [
                f"Person{index},Surname{index},{_URL},person{index}@example.com,Globex,Designer,23 Jul 2026,note"
                for index in range(rows)
            ]
        ),
        encoding="utf-8",
    )
    return source


def _json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    document = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def _stage(db_file: Path, source: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source), *extra, "--json"]) == 0
    return _json(capsys)


def _commit(db_file: Path, batch_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all", "--json"]) == 0
    return _json(capsys)


def _sources(db_file: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "sources", *extra, "--json"]) == 0
    return _json(capsys)


def _show(db_file: Path, session_id: str, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "source", "show", session_id, *extra, "--json"]) == 0
    return _json(capsys)


def test_a_staged_source_appears_in_the_listing_with_its_caller_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys, "--label", "Work connections", "--external-source-id", "crm-1")

    document = _sources(db_file, capsys)

    assert document["format"] == IMPORT_SOURCES_FORMAT
    assert document["version"] == IMPORT_SOURCES_VERSION
    assert document["limit"] == 50
    assert document["next_cursor"] is None
    assert len(document["sources"]) == 1
    source = document["sources"][0]
    assert source["id"] == batch["source_session_id"]
    assert source["source_kind"] == "linkedin"
    assert source["status"] == "staged"
    assert source["label"] == "Work connections"
    assert source["external_source_id"] == "crm-1"
    assert source["batch_id"] == batch["batch_id"]
    assert source["claimed"] is True
    assert source["redacted"] is False


def test_sources_are_listed_newest_first(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path, "a.csv"), capsys)
    second = _stage(db_file, _linkedin(tmp_path, "b.csv", rows=3), capsys)

    document = _sources(db_file, capsys)

    assert [entry["id"] for entry in document["sources"]] == [
        second["source_session_id"],
        first["source_session_id"],
    ]


def test_a_bounded_listing_page_is_traversed_by_its_cursor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    staged = [_stage(db_file, _linkedin(tmp_path, f"{index}.csv", rows=index + 1), capsys) for index in range(3)]

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = _sources(db_file, capsys, *(["--cursor", cursor] if cursor else []), "--limit", "1")
        assert len(page["sources"]) == 1
        seen.append(page["sources"][0]["id"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [entry["source_session_id"] for entry in reversed(staged)]


def test_showing_a_committed_source_reports_its_records(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys)
    commit = _commit(db_file, batch["batch_id"], capsys)

    document = _show(db_file, batch["source_session_id"], capsys)

    assert document["format"] == IMPORT_SOURCE_FORMAT
    assert document["version"] == IMPORT_SOURCE_VERSION
    assert document["source"]["status"] == "committed"
    assert document["counts"]["mappings_total"] == len(commit["committed_ids"])
    assert document["counts"]["mappings_by_disposition"] == {"entity": len(commit["committed_ids"])}
    assert document["counts"]["staged_by_status"] == {"committed": len(commit["committed_ids"])}
    assert [entry["candidate_id"] for entry in document["mappings"]] == sorted(commit["committed_ids"])
    assert all(entry["entity_id"] for entry in document["mappings"])


def test_a_bounded_mapping_page_is_traversed_by_its_cursor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=3), capsys)
    commit = _commit(db_file, batch["batch_id"], capsys)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = _show(
            db_file,
            batch["source_session_id"],
            capsys,
            "--limit",
            "2",
            *(["--cursor", cursor] if cursor else []),
        )
        assert len(page["mappings"]) <= 2
        # The aggregate describes the source, so it is the same on every page.
        assert page["counts"]["mappings_total"] == len(commit["committed_ids"])
        seen.extend(entry["candidate_id"] for entry in page["mappings"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == sorted(commit["committed_ids"])


def test_provenance_survives_the_staging_a_commit_leaves_behind(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A committed source still explains itself through mappings rather than staging rows."""
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys)
    _commit(db_file, batch["batch_id"], capsys)

    document = _show(db_file, batch["source_session_id"], capsys)

    assert document["counts"]["staged_by_status"] == {"committed": document["counts"]["mappings_total"]}
    assert document["mappings"]


def test_a_partial_commit_is_understandable_from_the_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=2), capsys)
    assert cli.main(["--db", str(db_file), "import", "review", batch["batch_id"], "--json"]) == 0
    review = _json(capsys)
    person_ids = [row["id"] for row in review["candidates"] if row["candidate"]["type"] == "person"]
    assert (
        cli.main(["--db", str(db_file), "import", "commit", batch["batch_id"], "--accept", person_ids[0], "--json"])
        == 0
    )
    capsys.readouterr()

    document = _show(db_file, batch["source_session_id"], capsys)

    assert document["source"]["status"] == "partially_committed"
    assert document["counts"]["mappings_total"] == 1
    assert document["counts"]["staged_by_status"]["committed"] == 1
    assert document["counts"]["staged_by_status"]["pending"] == len(review["candidates"]) - 1


def test_mappings_remain_readable_after_staging_rows_are_cleaned_up(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mappings outlive staging by design, so inspection must not depend on reviewable rows."""
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys)
    commit = _commit(db_file, batch["batch_id"], capsys)
    connection = open_db(db_file)
    try:
        with connection:
            connection.execute("DELETE FROM import_staging WHERE batch_id = ?", (batch["batch_id"],))
    finally:
        connection.close()

    document = _show(db_file, batch["source_session_id"], capsys)

    assert document["counts"]["staged_total"] == 0
    assert document["counts"]["mappings_total"] == len(commit["committed_ids"])
    assert [entry["candidate_id"] for entry in document["mappings"]] == sorted(commit["committed_ids"])


def test_completed_source_mappings_survive_a_bootstrap_restore(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys, "--label", "Work connections")
    commit = _commit(db_file, batch["batch_id"], capsys)
    outbox = tmp_path / "outbox"
    assert cli.main(["--db", str(db_file), "sync", "push", "--output", str(outbox)]) == 0
    capsys.readouterr()
    restored = tmp_path / "restored.db"
    assert (
        cli.main(["--db", str(restored), "sync", "pull", "--input", str(outbox / SYNC_BUNDLE_FILENAME), "--yes"]) == 0
    )
    capsys.readouterr()

    document = _show(restored, batch["source_session_id"], capsys)

    assert document["source"]["label"] == "Work connections"
    assert document["counts"]["mappings_total"] == len(commit["committed_ids"])
    assert [entry["candidate_id"] for entry in document["mappings"]] == sorted(commit["committed_ids"])


def test_a_hard_forget_scrubs_caller_metadata_while_survivors_remain_visible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(
        db_file,
        _linkedin(tmp_path, rows=2),
        capsys,
        "--label",
        "Interview notes",
        "--external-source-id",
        "crm-9",
    )
    _commit(db_file, batch["batch_id"], capsys)
    assert cli.main(["--db", str(db_file), "delete", "Person0 Surname0", "--yes"]) == 0
    capsys.readouterr()

    document = _show(db_file, batch["source_session_id"], capsys)

    # Opaque wording cannot be attributed to one of the people a source mentioned, so it goes.
    assert document["source"]["label"] is None
    assert document["source"]["external_source_id"] is None
    # The survivor's provenance is still there.
    assert document["counts"]["mappings_total"] > 0
    assert document["source"]["status"] != "redacted"


def test_a_fully_forgotten_claim_backed_source_shows_only_its_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=2), capsys, "--label", "Interview notes")
    _commit(db_file, batch["batch_id"], capsys)
    for index in range(2):
        assert cli.main(["--db", str(db_file), "delete", f"Person{index} Surname{index}", "--yes"]) == 0
    capsys.readouterr()

    document = _show(db_file, batch["source_session_id"], capsys, "--limit", str(MAX_SOURCE_PAGE_LIMIT))

    source = document["source"]
    assert source["status"] == "redacted"
    assert source["redacted"] is True
    assert source["claimed"] is True
    assert source["content_digest"]
    assert source["created_at"] is None
    assert source["batch_id"] is None
    assert source["label"] is None
    assert source["external_source_id"] is None
    assert source["extraction_contract_revision"] is None
    assert document["mappings"] == []
    assert document["counts"]["mappings_total"] == 0
    assert document["next_cursor"] is None


def test_a_fully_forgotten_digestless_source_has_no_row_to_inspect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a digest there is no duplicate claim worth retaining, so nothing is."""
    db_file = tmp_path / "people.db"
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps([{"type": "person", "ref": "p1", "name": "Priya Nair", "aliases": []}]),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "--db",
                str(db_file),
                "import",
                "stage-candidates",
                "--source",
                "planning sync",
                "--input",
                str(candidates),
                "--source-kind",
                "meeting_transcript",
                "--json",
            ]
        )
        == 0
    )
    batch = _json(capsys)
    session_id = batch["source_session_id"]
    assert session_id is not None
    _commit(db_file, batch["batch_id"], capsys)
    assert _show(db_file, session_id, capsys)["source"]["claimed"] is False
    assert cli.main(["--db", str(db_file), "delete", "Priya Nair", "--yes"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "source", "show", session_id]) == 1

    captured = capsys.readouterr()
    assert "unknown_source_session" in captured.err
    assert _sources(db_file, capsys)["sources"] == []


def test_a_redacted_source_is_equally_narrow_in_the_listing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=1), capsys, "--label", "Interview notes")
    _commit(db_file, batch["batch_id"], capsys)
    assert cli.main(["--db", str(db_file), "delete", "Person0 Surname0", "--yes"]) == 0
    capsys.readouterr()

    document = _sources(db_file, capsys)

    source = document["sources"][0]
    assert source["status"] == "redacted"
    assert source["label"] is None
    assert source["created_at"] is None
    assert source["batch_id"] is None


def test_json_output_still_carries_the_disclosure_reminder(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reminder belongs to the disclosure, not to one rendering of it."""
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys, "--label", "Work connections")

    assert cli.main(["--db", str(db_file), "sources", "--json"]) == 0
    listing = capsys.readouterr()
    assert "Import receipts are metadata about personal material" in listing.err
    # stderr is not the document: stdout still holds exactly one parseable JSON object.
    assert json.loads(listing.out)["sources"][0]["label"] == "Work connections"

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"], "--json"]) == 0
    detail = capsys.readouterr()
    assert "Import receipts are metadata about personal material" in detail.err
    assert json.loads(detail.out)["source"]["label"] == "Work connections"


def test_an_empty_listing_warns_about_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "sources", "--json"]) == 0

    captured = capsys.readouterr()
    assert "Import receipts are metadata" not in captured.err
    assert json.loads(captured.out)["sources"] == []


def test_a_cursor_from_another_source_is_refused_rather_than_skipping_provenance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    # Distinct content, or the duplicate claim would report one source twice and the cursor
    # under test would legitimately belong to the source it is being replayed against.
    first = _stage(db_file, _linkedin(tmp_path, "a.csv", rows=3), capsys)
    second = _stage(db_file, _linkedin(tmp_path, "b.csv", rows=4), capsys)
    assert first["source_session_id"] != second["source_session_id"]
    _commit(db_file, first["batch_id"], capsys)
    _commit(db_file, second["batch_id"], capsys)
    borrowed = _show(db_file, second["source_session_id"], capsys, "--limit", "2")["next_cursor"]
    assert borrowed is not None

    code = cli.main(["--db", str(db_file), "source", "show", first["source_session_id"], "--cursor", borrowed])

    assert code == 2
    captured = capsys.readouterr()
    assert "invalid_source_cursor" in captured.err
    assert captured.out == ""


def test_a_listing_cursor_is_refused_by_a_mapping_page(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    _stage(db_file, _linkedin(tmp_path, "a.csv"), capsys)
    batch = _stage(db_file, _linkedin(tmp_path, "b.csv", rows=3), capsys)
    listing_cursor = _sources(db_file, capsys, "--limit", "1")["next_cursor"]
    assert listing_cursor is not None

    code = cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"], "--cursor", listing_cursor])

    assert code == 2
    assert "invalid_source_cursor" in capsys.readouterr().err


def test_an_empty_database_lists_no_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "sources"]) == 0

    assert "No import sources." in capsys.readouterr().out


@pytest.mark.parametrize("limit", ["0", "201", "-1"])
def test_a_page_limit_outside_its_range_exits_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    limit: str,
) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "sources", "--limit", limit]) == 2

    captured = capsys.readouterr()
    assert "invalid_source_page_limit" in captured.err
    assert captured.out == ""


def test_an_unrecognized_cursor_exits_two_without_a_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "sources", "--cursor", "not-a-real-cursor!", "--json"]) == 2

    captured = capsys.readouterr()
    assert "invalid_source_cursor" in captured.err
    assert captured.out == ""


def test_an_unknown_source_session_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "source", "show", "01JZ0000000000000000000001"]) == 1

    captured = capsys.readouterr()
    assert "unknown_source_session" in captured.err
    assert captured.out == ""


def test_human_output_renders_the_source_without_raw_material(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source_file = _linkedin(tmp_path)
    batch = _stage(db_file, source_file, capsys, "--label", "Work connections")
    _commit(db_file, batch["batch_id"], capsys)

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"]]) == 0

    captured = capsys.readouterr()
    assert batch["source_session_id"] in captured.out
    assert "Work connections" in captured.out
    # Inspection is not a document browser: no path, no extraction configuration, no content.
    assert str(source_file) not in captured.out
    assert "Globex" not in captured.out
    assert "person0@example.com" not in captured.out
    assert "Import receipts are metadata about personal material" in captured.err


def test_human_listing_renders_a_table_and_offers_the_next_page_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path, "a.csv"), capsys, "--label", "Work connections")
    _stage(db_file, _linkedin(tmp_path, "b.csv", rows=3), capsys)

    assert cli.main(["--db", str(db_file), "sources", "--limit", "1"]) == 0

    captured = capsys.readouterr()
    assert "ID" in captured.out
    assert "KIND" in captured.out
    assert "linkedin" in captured.out
    assert "re-run this command with --cursor " in captured.out
    # A bare `pctx ...` line would drop the global --db this invocation used.
    assert "pctx sources" not in captured.out
    # Only the first page is rendered, so the older source is not in this output.
    assert first["source_session_id"] not in captured.out
    assert "Import receipts are metadata about personal material" in captured.err


def test_a_human_listing_shows_a_label_it_carries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    _stage(db_file, _linkedin(tmp_path), capsys, "--label", "Work connections")

    assert cli.main(["--db", str(db_file), "sources"]) == 0

    assert "Work connections" in capsys.readouterr().out


def test_human_output_for_a_redacted_source_stops_at_its_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=1), capsys, "--label", "Interview notes")
    _commit(db_file, batch["batch_id"], capsys)
    assert cli.main(["--db", str(db_file), "delete", "Person0 Surname0", "--yes"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"]]) == 0

    captured = capsys.readouterr()
    assert "This source's records were all forgotten" in captured.out
    assert "Interview notes" not in captured.out
    assert "created:" not in captured.out
    assert "batch:" not in captured.out
    assert "CANDIDATE" not in captured.out


def test_human_output_for_a_digestless_source_says_it_promises_no_deduplication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps([{"type": "person", "ref": "p1", "name": "Priya Nair", "aliases": []}]),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "--db",
                str(db_file),
                "import",
                "stage-candidates",
                "--source",
                "planning sync",
                "--input",
                str(candidates),
                "--source-kind",
                "meeting_transcript",
                "--json",
            ]
        )
        == 0
    )
    batch = _json(capsys)

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"]]) == 0

    captured = capsys.readouterr()
    assert "claim: none (this source makes no duplicate-import promise)" in captured.out
    # Nothing is committed yet, so the mapping page is legitimately empty.
    assert "No committed candidates on this page." in captured.out
    assert "staged candidates: 1 (pending: 1)" in captured.out


def test_human_output_offers_the_next_page_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path, rows=3), capsys)
    _commit(db_file, batch["batch_id"], capsys)

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"], "--limit", "1"]) == 0

    captured = capsys.readouterr()
    assert "re-run this command with --cursor " in captured.out
    assert "pctx source show" not in captured.out


def test_the_listing_and_detail_documents_are_byte_stable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _stage(db_file, _linkedin(tmp_path), capsys)
    _commit(db_file, batch["batch_id"], capsys)

    assert cli.main(["--db", str(db_file), "sources", "--json"]) == 0
    first_listing = capsys.readouterr().out
    assert cli.main(["--db", str(db_file), "sources", "--json"]) == 0
    assert capsys.readouterr().out == first_listing

    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"], "--json"]) == 0
    first_detail = capsys.readouterr().out
    assert cli.main(["--db", str(db_file), "source", "show", batch["source_session_id"], "--json"]) == 0
    assert capsys.readouterr().out == first_detail
