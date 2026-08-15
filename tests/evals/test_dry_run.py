"""The offline dry run: deterministic, network-free, and refusing real databases."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.harness import HARNESS_VERSION, REPORT_FORMAT, REPORT_VERSION
from evals.harness.__main__ import main
from evals.harness.errors import EvalHarnessError
from evals.harness.world import build_world_database, load_world, refuse_real_database

ROOT = Path(__file__).parents[2]
SUITE_PATH = ROOT / "evals" / "suite" / "suite.json"
WORLD_PATH = ROOT / "evals" / "suite" / "world.json"
RECORDED_REPORT = ROOT / "evals" / "results" / "2026-08-15-stub-dry-run.json"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


def _run(tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "report.json"
    exit_code = main(
        [
            "--suite",
            str(SUITE_PATH),
            "--runner",
            "stub",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            str(out),
            *extra,
        ],
        clock=_FixedClock(),
    )
    assert exit_code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_dry_run_opens_no_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dry run is what contributors and CI run; it must never need the network."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the stub dry run must not create a socket")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)

    report = _run(tmp_path)

    assert report["runner"] == {"name": "stub", "kind": "stub"}


def test_dry_run_is_byte_identical_across_runs(tmp_path: Path) -> None:
    first = _run(tmp_path / "a")
    second = _run(tmp_path / "b")

    assert first == second


def test_report_identifies_the_code_and_inputs_that_produced_it(tmp_path: Path) -> None:
    report = _run(tmp_path)

    assert report["format"] == REPORT_FORMAT
    assert report["version"] == REPORT_VERSION
    assert report["harness_version"] == HARNESS_VERSION
    assert report["generated_at"] == "2026-08-15T12:00:00Z"
    assert report["suite"] == {
        "id": "people-context-core",
        "version": "1.0.0",
        "world_id": "tidepool-2026-08",
        "world_as_of": "2026-08-01T09:00:00Z",
    }
    assert all(run["model_id"] == "stub/recorded-answers" for run in report["runs"])


def test_report_records_the_verbatim_prompts_it_scored(tmp_path: Path) -> None:
    """A published number is only checkable if the report carries the wording behind it."""
    report = _run(tmp_path)
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))

    assert report["prompts"]["system"] == suite["system_prompt"]
    assert [task["prompt"] for task in report["prompts"]["tasks"]] == [task["prompt"] for task in suite["tasks"]]


def test_every_task_runs_under_both_conditions(tmp_path: Path) -> None:
    report = _run(tmp_path)

    pairs = {(run["task_id"], run["condition"]) for run in report["runs"]}
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    expected = {(task["id"], condition) for task in suite["tasks"] for condition in ("with_mcp", "without_mcp")}
    assert pairs == expected


def test_runs_are_ordered_deterministically(tmp_path: Path) -> None:
    report = _run(tmp_path)

    keys = [(run["task_id"], run["condition"]) for run in report["runs"]]
    assert keys == sorted(keys)


def test_recorded_result_matches_a_fresh_dry_run(tmp_path: Path) -> None:
    """Regression: the published dry-run numbers must stay re-derivable from the suite."""
    recorded = json.loads(RECORDED_REPORT.read_text(encoding="utf-8"))
    fresh = _run(tmp_path)

    assert recorded["harness_version"] == fresh["harness_version"]
    assert recorded["suite"] == fresh["suite"]
    assert recorded["totals"] == fresh["totals"]
    assert [(run["task_id"], run["condition"], run["earned"]) for run in recorded["runs"]] == [
        (run["task_id"], run["condition"], run["earned"]) for run in fresh["runs"]
    ]


def test_task_and_condition_selection_narrow_the_run(tmp_path: Path) -> None:
    report = _run(tmp_path, "--only", "relationship-path", "--condition", "with_mcp")

    assert [(run["task_id"], run["condition"]) for run in report["runs"]] == [("relationship-path", "with_mcp")]
    assert [total["condition"] for total in report["totals"]] == ["with_mcp"]


def test_the_report_file_is_owner_only(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    main(
        ["--suite", str(SUITE_PATH), "--runner", "stub", "--workdir", str(tmp_path / "work"), "--out", str(out)],
        clock=_FixedClock(),
    )

    assert out.stat().st_mode & 0o777 == 0o600


def test_a_missing_transcript_refuses_rather_than_scoring_silence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    transcripts = json.loads((SUITE_PATH.parent / "stub-transcripts.json").read_text(encoding="utf-8"))
    transcripts["transcripts"] = [
        item for item in transcripts["transcripts"] if item["task_id"] != "relationship-path"
    ]
    (tmp_path / "world.json").write_text(WORLD_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "partial.json").write_text(json.dumps(transcripts), encoding="utf-8")
    suite["runners"]["stub"]["transcripts"] = "partial.json"
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")

    exit_code = main(
        ["--suite", str(suite_path), "--runner", "stub", "--workdir", str(tmp_path / "work")],
        clock=_FixedClock(),
    )

    assert exit_code == 1
    assert "no stub transcript recorded for task relationship-path" in capsys.readouterr().err


def test_the_harness_refuses_the_configured_personal_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong --workdir must not be able to touch the store the user actually uses."""
    personal = tmp_path / "people.db"
    monkeypatch.setenv("PEOPLE_CONTEXT_DB", str(personal))

    with pytest.raises(EvalHarnessError, match="refusing to evaluate against the configured"):
        refuse_real_database(personal)


def test_the_harness_refuses_any_database_that_already_exists(tmp_path: Path) -> None:
    existing = tmp_path / "world.db"
    existing.write_bytes(b"")

    with pytest.raises(EvalHarnessError, match="refusing to reuse an existing database"):
        refuse_real_database(existing)


def test_the_harness_refuses_an_existing_wal_companion(tmp_path: Path) -> None:
    """A stray -wal file means a live database; opening it would migrate someone's data."""
    target = tmp_path / "world.db"
    Path(f"{target}-wal").write_bytes(b"")

    with pytest.raises(EvalHarnessError, match="refusing to reuse an existing database"):
        refuse_real_database(target)


def test_the_materialized_world_is_attributed_to_the_fixture(tmp_path: Path) -> None:
    world = load_world(WORLD_PATH)

    db_path = build_world_database(world, tmp_path / "world.db")

    assert db_path.is_file()
    from people_context.adapters.runtime import build_runtime

    runtime = build_runtime(db_path)
    try:
        people = runtime.repo.list_people()
        names = sorted(person.canonical_name for person in people)
        selves = [person for person in people if person.is_self]
    finally:
        runtime.close()
    assert names == [
        "Ingrid Solberg",
        "Kofi Mensah",
        "Noor Vance",
        "Priya Raman",
        "Priya Ramanathan",
        "Tomas Brandt",
    ]
    assert [person.canonical_name for person in selves] == ["Noor Vance"]
