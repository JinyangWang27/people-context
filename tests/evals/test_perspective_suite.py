"""The M31.2 perspective suite: fictional fixtures, separated answer keys, and a human-review baseline."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest
from evals.harness.report import build_report, render_summary
from evals.harness.runner import RunOutcome, prepare_workspace
from evals.harness.runners.command import CommandAgentRunner
from evals.harness.scoring import TaskScore
from evals.harness.suite import CommandRunnerConfig, load_suite
from evals.harness.world import World, load_world

ROOT = Path(__file__).parents[2]
SUITE_PATH = ROOT / "evals" / "perspective" / "suite.json"
REVIEW_KEY = ROOT / "evals" / "perspective" / "review.md"
CORE_SUITE_PATH = ROOT / "evals" / "suite" / "suite.json"


def _suite_document() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _world_document() -> dict:
    return json.loads((SUITE_PATH.parent / "world.json").read_text(encoding="utf-8"))


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_perspective_suite_is_human_reviewed_and_unscored() -> None:
    loaded = load_suite(SUITE_PATH)

    assert loaded.suite.review == "human"
    assert all(not task.rubric for task in loaded.suite.tasks)
    assert "stub" not in loaded.suite.runners, "authored answers must not sit beside model output"


def test_a_human_suite_carrying_a_rubric_is_refused(tmp_path: Path) -> None:
    document = _suite_document()
    (tmp_path / "world.json").write_text((SUITE_PATH.parent / "world.json").read_text(encoding="utf-8"))
    document["tasks"][0]["rubric"] = [
        {"id": "x", "description": "x", "kind": "answer_contains_all", "values": ["Dana"]}
    ]

    with pytest.raises(EvalHarnessError, match="must not carry rubrics"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_a_rubric_suite_task_without_a_rubric_is_refused(tmp_path: Path) -> None:
    document = json.loads(CORE_SUITE_PATH.read_text(encoding="utf-8"))
    document["tasks"][0]["rubric"] = []

    with pytest.raises(EvalHarnessError, match="needs a rubric"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_the_scenarios_span_the_required_languages_and_cases() -> None:
    ids = {task.id for task in load_suite(SUITE_PATH).suite.tasks}

    assert {
        "grounded-hiring-en",
        "conflicting-plans-zh",
        "sparse-family-zh",
        "ambiguous-sam-en",
        "unfamiliar-question-en",
        "public-figure-packet-en",
        "coaching-pushback-en",
        "coaching-decline-zh",
        "lookup-trigger-en",
    } == ids


def test_every_task_has_a_review_key_entry() -> None:
    key = REVIEW_KEY.read_text(encoding="utf-8")

    for task in load_suite(SUITE_PATH).suite.tasks:
        assert f"## {task.id}\n" in key, f"{task.id} has no review key"


def test_the_review_key_is_kept_apart_from_answering_agent_material() -> None:
    """Expected answers must never reach the agent through the suite, a prompt, or the staged plugin."""
    raw = SUITE_PATH.read_text(encoding="utf-8")

    assert "review.md" not in raw
    assert "review key" not in raw.lower()


def test_every_perspective_contact_uses_a_reserved_test_domain() -> None:
    world = load_world(SUITE_PATH.parent / "world.json")

    handles = [handle for person in world.people for handle in person.handles]
    assert handles
    assert all(handle.endswith("@example.test") for handle in handles), handles


def test_a_trait_citing_an_unknown_observation_is_refused() -> None:
    document = _world_document()
    document["traits"][0]["evidence_keys"].append("nowhere")

    with pytest.raises(ValueError, match="traits cite unknown observation keys: nowhere"):
        World.model_validate(document)


def test_a_trait_citing_another_persons_observation_is_refused() -> None:
    """Regression: the schema refuses cross-subject evidence instead of the build crashing mid-materialization."""
    document = _world_document()
    document["traits"][0]["evidence_keys"].append("jiahe-plans-ahead")

    with pytest.raises(ValueError, match="traits cite observations about another person: jiahe-plans-ahead"):
        World.model_validate(document)


def test_a_world_with_cross_subject_evidence_is_refused_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from evals.harness.__main__ import main

    world = _world_document()
    world["traits"][0]["evidence_keys"].append("jiahe-plans-ahead")
    _write(tmp_path / "world.json", world)
    _write(tmp_path / "suite.json", _suite_document())

    code = main(["--suite", str(tmp_path / "suite.json"), "--runner", "claude-cli", "--workdir", str(tmp_path / "w")])

    assert code == 1
    assert "invalid evaluation world" in capsys.readouterr().err
    assert not (tmp_path / "w").exists()


def test_duplicate_observation_keys_are_refused() -> None:
    document = _world_document()
    document["observations"].append(dict(document["observations"][0]))

    with pytest.raises(ValueError, match="observation keys must be unique"):
        World.model_validate(document)


def test_an_observation_about_an_unknown_person_is_refused() -> None:
    document = _world_document()
    document["observations"][0]["person_key"] = "nobody"

    with pytest.raises(ValueError, match="references to unknown person keys: nobody"):
        World.model_validate(document)


def test_naive_observation_timestamps_are_refused() -> None:
    document = _world_document()
    document["observations"][0]["observed_at"] = "2026-03-10T14:00:00"

    with pytest.raises(ValueError, match="timezone-aware"):
        World.model_validate(document)


def test_the_world_materializes_statements_reports_and_trait_evidence(tmp_path: Path) -> None:
    world = load_world(SUITE_PATH.parent / "world.json")

    workspace = prepare_workspace(world, tmp_path, ())

    with sqlite3.connect(workspace.pristine) as connection:
        observations = connection.execute("SELECT count(*) FROM observations").fetchone()[0]
        stated_by = {row[0] for row in connection.execute("SELECT provenance_stated_by FROM observations")}
        links = connection.execute("SELECT count(*) FROM trait_evidence").fetchone()[0]
    assert observations == len(world.observations)
    assert {"Dana Whitlock", "Omar Haddad", "陈嘉禾", "许蔓"} <= stated_by
    assert links == sum(len(trait.evidence_keys) for trait in world.traits)


def test_the_staged_plugin_carries_the_skills_and_no_mcp_server(tmp_path: Path) -> None:
    """The shipped manifest would start a server on the operator's real store; the staged one must not."""
    world = load_world(SUITE_PATH.parent / "world.json")

    plugin = prepare_workspace(world, tmp_path, ()).skills_plugin

    assert plugin is not None
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "mcpServers" not in manifest
    assert (plugin / "skills" / "person-perspective" / "SKILL.md").is_file()
    assert (plugin / "skills" / "communication-coach" / "SKILL.md").is_file()
    assert not list(plugin.rglob("review.md"))


def test_the_skills_plugin_placeholder_is_substituted_whole(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    config = CommandRunnerConfig.model_validate(
        {
            "kind": "command",
            "model_id": "fake/echo-1",
            "argv": [sys.executable, str(script), "--plugin-dir", "{skills_plugin}", "{system_prompt}", "{prompt}"],
            "timeout_seconds": 30,
            "max_output_bytes": 4096,
        }
    )
    request = AgentRequest(
        task_id="t",
        condition="without_mcp",
        system_prompt="s",
        prompt="p",
        mcp_config_path=None,
        working_directory=tmp_path,
        skills_plugin=tmp_path / "plugin",
    )

    argv = json.loads(CommandAgentRunner(config).run(request).answer)

    assert argv == ["--plugin-dir", str(tmp_path / "plugin"), "s", "p"]
    unstaged = AgentRequest(**{**request.__dict__, "skills_plugin": None})
    with pytest.raises(EvalHarnessError, match="no skills plugin was staged"):
        CommandAgentRunner(config).run(unstaged)


def test_a_human_report_carries_no_totals_and_keeps_v1_field_types(tmp_path: Path) -> None:
    """Regression: v1 run fields stay numeric; `review` and empty totals are what mark a report unscored."""
    from datetime import UTC, datetime

    loaded = load_suite(SUITE_PATH)
    world = load_world(loaded.world_path)
    task = loaded.suite.tasks[0]
    calls = ({"name": "mcp__people-context__resolve_person", "input": {"name": "Dana Whitlock"}},)
    observed = RunOutcome(task.id, "with_mcp", "m", "answer", TaskScore(earned=0, possible=0, criteria=()), calls)
    unobserved = RunOutcome(task.id, "without_mcp", "m", "answer", TaskScore(earned=0, possible=0, criteria=()))

    report = build_report(
        loaded,
        world,
        (task,),
        (observed, unobserved),
        runner_name="r",
        runner_kind="command",
        generated_at=datetime(2026, 9, 23, tzinfo=UTC),
    )

    assert report["review"] == "human"
    assert report["totals"] == []
    for run in report["runs"]:
        assert isinstance(run["earned"], int) and isinstance(run["possible"], int)
        assert isinstance(run["percent"], float)
    assert report["runs"][0]["tool_calls"] == [dict(calls[0])]
    assert report["runs"][1]["tool_calls"] is None
    summary = render_summary(report)
    assert "human review — not scored" in summary
    assert "percent" not in summary
    assert "| 1 |" in summary and "unobserved" in summary


_STREAM_AGENT = """
import json, sys
events = [
    {"type": "system", "subtype": "init", "cwd": "/private/path/never/recorded"},
    {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "private reasoning"},
        {"type": "tool_use", "id": "a", "name": "Skill", "input": {"skill": "people-context:person-perspective"}},
    ]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "skill body"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "b", "name": "mcp__people-context__resolve_person", "input": {"name": "Sam"}},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "  Which Sam do you mean?  "},
]
mode = sys.argv[-1]
if mode == "error":
    events[-1] = {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""}
if mode == "truncated":
    events = events[:-1]
if mode == "garbage":
    print("not json")
for event in events:
    print(json.dumps(event))
"""


def _stream_runner(tmp_path: Path) -> CommandAgentRunner:
    script = tmp_path / "stream.py"
    script.write_text(_STREAM_AGENT, encoding="utf-8")
    config = CommandRunnerConfig.model_validate(
        {
            "kind": "command",
            "model_id": "fake/stream-1",
            "argv": [sys.executable, str(script), "{system_prompt}", "{prompt}"],
            "output_format": "stream-json",
            "timeout_seconds": 30,
            "max_output_bytes": 65536,
        }
    )
    return CommandAgentRunner(config)


def _stream_request(tmp_path: Path, prompt: str) -> AgentRequest:
    return AgentRequest(
        task_id="ambiguous-sam-en",
        condition="without_mcp",
        system_prompt="s",
        prompt=prompt,
        mcp_config_path=None,
        working_directory=tmp_path,
    )


def test_a_stream_json_runner_records_every_tool_call_and_only_the_final_answer(tmp_path: Path) -> None:
    """Regression: a guessed read or a write must be visible to the reviewer even when the answer hides it."""
    response = _stream_runner(tmp_path).run(_stream_request(tmp_path, "ok"))

    assert response.answer == "Which Sam do you mean?"
    assert response.tool_calls == (
        {"name": "Skill", "input": {"skill": "people-context:person-perspective"}},
        {"name": "mcp__people-context__resolve_person", "input": {"name": "Sam"}},
    )
    assert "private" not in json.dumps(response.tool_calls)


@pytest.mark.parametrize(
    ("mode", "message"),
    [("error", "error result"), ("truncated", "without a result"), ("garbage", "not JSON lines")],
)
def test_an_unreadable_event_stream_is_refused(tmp_path: Path, mode: str, message: str) -> None:
    with pytest.raises(EvalHarnessError, match=message):
        _stream_runner(tmp_path).run(_stream_request(tmp_path, mode))


def test_a_text_runner_reports_tool_use_as_unobserved(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text("print('plain answer')\n", encoding="utf-8")
    config = CommandRunnerConfig.model_validate(
        {
            "kind": "command",
            "model_id": "fake/text-1",
            "argv": [sys.executable, str(script), "{system_prompt}", "{prompt}"],
            "timeout_seconds": 30,
            "max_output_bytes": 4096,
        }
    )

    response = CommandAgentRunner(config).run(_stream_request(tmp_path, "p"))

    assert response.answer == "plain answer"
    assert response.tool_calls is None


def test_the_perspective_runner_observes_tool_use() -> None:
    runner = load_suite(SUITE_PATH).runner_config("claude-cli")

    assert isinstance(runner, CommandRunnerConfig)
    assert runner.output_format == "stream-json"
    assert runner.argv[runner.argv.index("--output-format") + 1] == "stream-json"
