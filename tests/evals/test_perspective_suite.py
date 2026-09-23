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


def test_a_human_report_carries_no_totals_or_scores(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    loaded = load_suite(SUITE_PATH)
    world = load_world(loaded.world_path)
    task = loaded.suite.tasks[0]
    outcome = RunOutcome(task.id, "with_mcp", "m", "answer", TaskScore(earned=0, possible=0, criteria=()))

    report = build_report(
        loaded,
        world,
        (task,),
        (outcome,),
        runner_name="r",
        runner_kind="command",
        generated_at=datetime(2026, 9, 23, tzinfo=UTC),
    )

    assert report["review"] == "human"
    assert report["totals"] == []
    assert report["runs"][0]["percent"] is None
    assert "human review — not scored" in render_summary(report)
    assert "percent" not in render_summary(report)
