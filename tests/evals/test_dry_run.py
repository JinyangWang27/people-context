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
from evals.harness.ports import AgentRequest, AgentResponse
from evals.harness.runner import (
    ARTIFACTS_DIRNAME,
    WORLD_DB_FILENAME,
    prepare_workspace,
    resolve_server_argv,
)
from evals.harness.runners.stub import StubAgentRunner
from evals.harness.suite import CommandRunnerConfig, load_suite
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

    assert report["runner"] == {"name": "stub", "kind": "stub", "mcp_server_argv": []}


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


def test_report_carries_the_exact_rule_each_criterion_applied(tmp_path: Path) -> None:
    """A published score must stay re-derivable after the suite moves on."""
    report = _run(tmp_path)
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    rubrics = {task["id"]: {item["id"]: item for item in task["rubric"]} for task in suite["tasks"]}

    for run in report["runs"]:
        for criterion in run["criteria"]:
            source = rubrics[run["task_id"]][criterion["id"]]
            assert criterion["values"] == source.get("values"), criterion["id"]
            assert criterion["pattern"] == source.get("pattern"), criterion["id"]
            assert criterion["values"] is not None or criterion["pattern"] is not None


def test_the_agent_never_runs_where_the_fictional_database_lives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a control agent that could read world.db would not be a control."""
    workdir = tmp_path / "work"
    database = (workdir / ARTIFACTS_DIRNAME / WORLD_DB_FILENAME).resolve()
    # The agent directory is a temporary directory removed when the run ends, so its
    # contents have to be inspected while the agent would be looking at them.
    seen: list[tuple[Path, list[str]]] = []
    original = StubAgentRunner.run

    def _record(self: StubAgentRunner, request: AgentRequest) -> AgentResponse:
        directory = request.working_directory.resolve()
        seen.append((directory, sorted(entry.name for entry in directory.iterdir())))
        return original(self, request)

    monkeypatch.setattr(StubAgentRunner, "run", _record)
    main(
        ["--suite", str(SUITE_PATH), "--runner", "stub", "--workdir", str(workdir)],
        clock=_FixedClock(),
    )

    assert seen
    assert database.is_file(), "the fixture must still have been built"
    for directory, entries in seen:
        assert entries == [], f"the agent directory must be empty: {directory}"
        assert directory != database.parent
        assert directory not in database.parents, "the fixture must not sit under the agent directory"
        assert workdir.resolve() not in directory.parents, "the agent must not run inside the artifacts tree"


def test_each_invocation_gets_its_own_agent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a shared directory let with_mcp session state reach the control run."""
    seen: list[Path] = []
    original = StubAgentRunner.run

    def _record(self: StubAgentRunner, request: AgentRequest) -> AgentResponse:
        seen.append(request.working_directory.resolve())
        return original(self, request)

    monkeypatch.setattr(StubAgentRunner, "run", _record)
    main(
        ["--suite", str(SUITE_PATH), "--runner", "stub", "--workdir", str(tmp_path / "work")],
        clock=_FixedClock(),
    )

    assert len(seen) == 10
    assert len(set(seen)) == len(seen), "two invocations shared a working directory"


def test_a_report_destination_that_would_replace_the_real_database_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: a mistyped --out atomically replaced the user's store with report JSON."""
    personal = tmp_path / "people.db"
    personal.write_bytes(b"real store")
    monkeypatch.setenv("PEOPLE_CONTEXT_DB", str(personal))

    exit_code = main(
        [
            "--suite",
            str(SUITE_PATH),
            "--runner",
            "stub",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            str(personal),
        ],
        clock=_FixedClock(),
    )

    assert exit_code == 1
    assert "refusing to write the report to" in capsys.readouterr().err
    assert personal.read_bytes() == b"real store"


def test_a_report_destination_naming_a_database_sidecar_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal = tmp_path / "people.db"
    personal.write_bytes(b"real store")
    monkeypatch.setenv("PEOPLE_CONTEXT_DB", str(personal))

    exit_code = main(
        [
            "--suite",
            str(SUITE_PATH),
            "--runner",
            "stub",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            f"{personal}-wal",
        ],
        clock=_FixedClock(),
    )

    assert exit_code == 1
    assert not Path(f"{personal}-wal").exists()


def test_the_evaluated_server_is_pinned_to_this_checkout() -> None:
    """An unpinned PyPI resolution would let a later release answer the same suite."""
    loaded = load_suite(SUITE_PATH)
    config = loaded.runner_config("claude-cli")

    assert isinstance(config, CommandRunnerConfig)
    assert "{project_root}" in config.mcp_server_argv
    assert "people-context" not in config.mcp_server_argv, "the bare PyPI name is not a pin"
    resolved = resolve_server_argv(config.mcp_server_argv, datetime(2026, 8, 1, 9, 0, tzinfo=UTC))
    assert str(ROOT) in resolved
    assert "2026-08-01T09:00:00Z" in resolved, "the server must be frozen at the fixture instant"


def test_an_unknown_server_placeholder_is_refused() -> None:
    with pytest.raises(EvalHarnessError, match="is not a whole placeholder"):
        resolve_server_argv(("uv", "run", "--project", "{checkout}"), datetime(2026, 8, 1, tzinfo=UTC))


def test_the_evaluated_server_is_frozen_at_the_fixture_instant(tmp_path: Path) -> None:
    """Regression: on the system clock, recency answers change with the execution date."""
    world = load_world(WORLD_PATH)
    database = build_world_database(world, tmp_path / "world.db")

    at_fixture = _stale_days(database, world.as_of)
    a_year_later = _stale_days(database, world.as_of.replace(year=world.as_of.year + 1))

    assert at_fixture, "the fixture must produce recency rows"
    assert at_fixture != a_year_later, "recency output depends on the clock, so it must be pinned"


def _stale_days(database: Path, now: datetime) -> list[int | None]:
    """Return the reported days-since values for one clock."""
    from evals.harness.world import FixedClock
    from people_context.adapters.runtime import build_runtime

    runtime = build_runtime(database, clock=FixedClock(now))
    try:
        report = runtime.use_cases.get_stale_relationships.execute()
    finally:
        runtime.close()
    return [person.days_since for person in report.people]


def test_the_server_wrapper_refuses_a_naive_instant() -> None:
    from evals.harness.server import parse_instant

    with pytest.raises(ValueError, match="timezone-aware"):
        parse_instant("2026-08-01T09:00:00")


def test_the_server_wrapper_accepts_the_recorded_instant_form() -> None:
    from evals.harness.server import parse_instant

    assert parse_instant("2026-08-01T09:00:00Z") == datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def test_each_with_mcp_invocation_gets_its_own_copy_of_the_store(tmp_path: Path) -> None:
    """Regression: a shared store let a write tool in one task change every later one."""
    world = load_world(WORLD_PATH)
    workspace = prepare_workspace(world, tmp_path / "work", ("people-context-mcp",))

    first = workspace.configuration_for("identity-disambiguation", "with_mcp")
    second = workspace.configuration_for("relationship-path", "with_mcp")

    assert first is not None and second is not None
    assert first != second
    first_db = json.loads(first.read_text(encoding="utf-8"))["mcpServers"]["people-context"]["args"][-1]
    second_db = json.loads(second.read_text(encoding="utf-8"))["mcpServers"]["people-context"]["args"][-1]
    assert first_db != second_db
    assert Path(first_db).read_bytes() == Path(second_db).read_bytes() == workspace.pristine.read_bytes()
    assert Path(first_db) != workspace.pristine, "the pristine store must never be handed out"


def test_a_mutation_during_one_task_cannot_reach_the_next(tmp_path: Path) -> None:
    """The evaluated server exposes write and destructive tools; this bounds their blast radius."""
    world = load_world(WORLD_PATH)
    workspace = prepare_workspace(world, tmp_path / "work", ("people-context-mcp",))

    first = workspace.configuration_for("identity-disambiguation", "with_mcp")
    assert first is not None
    mutated = Path(json.loads(first.read_text(encoding="utf-8"))["mcpServers"]["people-context"]["args"][-1])
    mutated.write_bytes(b"the agent scribbled here")

    second = workspace.configuration_for("relationship-path", "with_mcp")
    assert second is not None
    fresh = Path(json.loads(second.read_text(encoding="utf-8"))["mcpServers"]["people-context"]["args"][-1])
    assert fresh.read_bytes() == workspace.pristine.read_bytes()


def test_the_control_condition_is_given_no_configuration(tmp_path: Path) -> None:
    world = load_world(WORLD_PATH)
    workspace = prepare_workspace(world, tmp_path / "work", ("people-context-mcp",))

    assert workspace.configuration_for("identity-disambiguation", "without_mcp") is None


def test_an_unwritable_report_destination_still_surfaces_the_completed_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: a paid model run could finish and then vanish in a traceback."""
    exit_code = main(
        [
            "--suite",
            str(SUITE_PATH),
            "--runner",
            "stub",
            "--workdir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "missing" / "report.json"),
        ],
        clock=_FixedClock(),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Cannot write the report to" in captured.err
    recovered = json.loads(captured.out[captured.out.index("{") :])
    assert recovered["totals"], "the completed report must be recoverable from stdout"
    assert "| with_mcp | 5 |" in captured.out, "the summary must be printed before the write is attempted"
