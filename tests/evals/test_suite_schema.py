"""The suite and world documents must fail closed rather than skip a criterion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.errors import EvalHarnessError
from evals.harness.suite import CommandRunnerConfig, LoadedSuite, load_suite
from evals.harness.world import World, load_world

SUITE_PATH = Path(__file__).parents[2] / "evals" / "suite" / "suite.json"


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _shipped_suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def test_shipped_suite_and_world_load() -> None:
    loaded = load_suite(SUITE_PATH)
    world = load_world(loaded.world_path)

    assert loaded.suite.suite_id == "people-context-core"
    assert len(loaded.suite.tasks) == 5
    assert world.world_id == "tidepool-2026-08"


def test_every_shipped_task_has_a_reachable_and_losable_score() -> None:
    """A rubric that cannot fail, or cannot pass, measures nothing."""
    loaded = load_suite(SUITE_PATH)

    for task in loaded.suite.tasks:
        assert task.possible_weight >= 1
        assert len(task.rubric) >= 2, f"{task.id} scores on a single criterion"


def test_unknown_suite_key_is_refused(tmp_path: Path) -> None:
    document = _shipped_suite()
    document["unexpected"] = True

    with pytest.raises(EvalHarnessError, match="invalid evaluation suite"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_uncompilable_criterion_pattern_is_refused(tmp_path: Path) -> None:
    document = _shipped_suite()
    document["tasks"][0]["rubric"][0] = {
        "id": "broken",
        "kind": "answer_matches",
        "description": "not a regular expression",
        "pattern": "(unclosed",
    }

    with pytest.raises(EvalHarnessError, match="invalid regular expression"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_duplicate_task_ids_are_refused(tmp_path: Path) -> None:
    document = _shipped_suite()
    document["tasks"].append(document["tasks"][0])

    with pytest.raises(EvalHarnessError, match="task ids must be unique"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_runner_may_not_forward_store_configuration_to_an_agent(tmp_path: Path) -> None:
    """Regression: forwarding PEOPLE_CONTEXT_DB would point the agent at a real store."""
    document = _shipped_suite()
    document["runners"]["claude-cli"]["env_passthrough"] = ["PATH", "PEOPLE_CONTEXT_DB"]

    with pytest.raises(EvalHarnessError, match="must not forward store configuration"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_encryption_key_may_not_be_forwarded_either(tmp_path: Path) -> None:
    document = _shipped_suite()
    document["runners"]["claude-cli"]["env_passthrough"] = ["PEOPLE_CONTEXT_DB_KEY"]

    with pytest.raises(EvalHarnessError, match="must not forward store configuration"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_half_configured_mcp_wiring_is_refused(tmp_path: Path) -> None:
    document = _shipped_suite()
    document["runners"]["claude-cli"]["mcp_server_argv"] = []

    with pytest.raises(EvalHarnessError, match="must be configured together"):
        load_suite(_write(tmp_path / "suite.json", document))


def test_shipped_command_runner_isolates_the_without_mcp_condition() -> None:
    """Without strict config the control run could silently load the operator's own server."""
    loaded = load_suite(SUITE_PATH)
    config = loaded.runner_config("claude-cli")

    assert isinstance(config, CommandRunnerConfig)
    assert "--strict-mcp-config" in config.argv
    assert "--mcp-config" not in config.argv
    assert "{mcp_config}" in config.mcp_argv


def test_shipped_command_runner_reads_the_api_key_only_from_the_environment() -> None:
    loaded = load_suite(SUITE_PATH)
    config = loaded.runner_config("claude-cli")

    assert isinstance(config, CommandRunnerConfig)
    assert "ANTHROPIC_API_KEY" in config.env_passthrough
    serialized = json.dumps(_shipped_suite())
    assert "sk-" not in serialized, "no key material may appear in the suite"


def test_unknown_runner_names_the_configured_ones() -> None:
    loaded = load_suite(SUITE_PATH)

    with pytest.raises(EvalHarnessError, match="unknown runner"):
        loaded.runner_config("nope")


def test_unknown_task_selection_is_refused() -> None:
    loaded = load_suite(SUITE_PATH)

    with pytest.raises(EvalHarnessError, match="unknown task ids: missing"):
        loaded.select_tasks(("missing",))


def test_task_selection_preserves_suite_order() -> None:
    loaded = load_suite(SUITE_PATH)

    selected = loaded.select_tasks(("stale-follow-up", "identity-disambiguation"))

    assert [task.id for task in selected] == ["identity-disambiguation", "stale-follow-up"]


def test_suite_assets_may_not_escape_the_suite_directory(tmp_path: Path) -> None:
    loaded = LoadedSuite(load_suite(SUITE_PATH).suite, tmp_path)

    with pytest.raises(EvalHarnessError, match="escapes the suite directory"):
        loaded.asset_path("../../etc/passwd")


def test_oversized_documents_are_refused(tmp_path: Path) -> None:
    oversized = tmp_path / "suite.json"
    oversized.write_text(" " * 1_048_577, encoding="utf-8")

    with pytest.raises(EvalHarnessError, match="larger than"):
        load_suite(oversized)


def _shipped_world() -> dict:
    return json.loads((SUITE_PATH.parent / "world.json").read_text(encoding="utf-8"))


def test_world_requires_exactly_one_self() -> None:
    document = _shipped_world()
    document["people"][1]["is_self"] = True

    with pytest.raises(ValueError, match="exactly one person must be marked is_self"):
        World.model_validate(document)


def test_world_refuses_dangling_person_references() -> None:
    document = _shipped_world()
    document["facts"].append({"person_key": "nobody", "predicate": "location", "value": "Nowhere"})

    with pytest.raises(ValueError, match="references to unknown person keys: nobody"):
        World.model_validate(document)


def test_world_refuses_naive_interaction_timestamps() -> None:
    document = _shipped_world()
    document["interactions"][0]["occurred_at"] = "2026-06-11T09:00:00"

    with pytest.raises(ValueError, match="timezone-aware"):
        World.model_validate(document)


def test_every_shipped_contact_uses_a_reserved_test_domain() -> None:
    """The fixture must stay unmistakably fictional, including its addresses."""
    world = load_world(SUITE_PATH.parent / "world.json")

    handles = [handle for person in world.people for handle in person.handles]
    assert handles
    for handle in handles:
        assert handle.endswith("@example.test"), handle
