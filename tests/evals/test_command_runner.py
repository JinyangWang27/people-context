"""The only place the harness starts a process, so the only place it can go wrong."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest
from evals.harness.runner import write_mcp_config
from evals.harness.runners.command import CommandAgentRunner
from evals.harness.suite import CommandRunnerConfig

#: CPython's C locale coercion (PEP 538) puts LC_CTYPE into a child that has no
#: locale variables, so it appears regardless of the allowlist and is discounted here.
_INTERPRETER_ADDED = {"LC_CTYPE"}

#: A fake agent: it reports its own argument vector and environment as JSON.
_ECHO_AGENT = """
import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ)}))
"""

_FAILING_AGENT = """
import sys
print("boom: model unavailable", file=sys.stderr)
sys.exit(3)
"""

_SILENT_AGENT = "pass\n"

_SLOW_AGENT = """
import time
time.sleep(30)
"""

_NOISY_AGENT = """
print("x" * 5000)
"""

_RUNAWAY_AGENT = """
import sys
while True:
    sys.stdout.write("x" * 4096)
    sys.stdout.flush()
"""


def _agent(tmp_path: Path, source: str, name: str = "agent.py") -> Path:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return script


def _config(script: Path, **overrides: object) -> CommandRunnerConfig:
    payload: dict[str, object] = {
        "kind": "command",
        "model_id": "fake/echo-1",
        "argv": [sys.executable, str(script), "--model", "{model}", "--system", "{system_prompt}", "{prompt}"],
        "mcp_argv": ["--mcp-config", "{mcp_config}"],
        "mcp_server_argv": ["people-context-mcp"],
        "timeout_seconds": 30,
        "max_output_bytes": 1024,
        "env_passthrough": [],
    }
    payload.update(overrides)
    return CommandRunnerConfig.model_validate(payload)


def _request(tmp_path: Path, condition: str, mcp_config_path: Path | None = None) -> AgentRequest:
    return AgentRequest(
        task_id="identity-disambiguation",
        condition=condition,
        system_prompt="be concise",
        prompt="Which Priya works at Kestrel Analytics?",
        mcp_config_path=mcp_config_path,
        working_directory=tmp_path,
    )


def test_prompt_is_passed_as_one_whole_argument(tmp_path: Path) -> None:
    """A prompt spliced into a command string could become extra flags; this one cannot."""
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT)))

    response = runner.run(_request(tmp_path, "without_mcp"))

    argv = json.loads(response.answer)["argv"]
    assert argv == [
        "--model",
        "fake/echo-1",
        "--system",
        "be concise",
        "Which Priya works at Kestrel Analytics?",
    ]
    assert response.model_id == "fake/echo-1"


def test_the_mcp_config_is_appended_only_in_the_with_mcp_condition(tmp_path: Path) -> None:
    config_path = write_mcp_config(tmp_path / "mcp.json", ("people-context-mcp",), tmp_path / "world.db")
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT)))

    with_mcp = json.loads(runner.run(_request(tmp_path, "with_mcp", config_path)).answer)["argv"]
    without_mcp = json.loads(runner.run(_request(tmp_path, "without_mcp")).answer)["argv"]

    assert with_mcp[-2:] == ["--mcp-config", str(config_path)]
    assert "--mcp-config" not in without_mcp


def test_the_written_mcp_config_names_the_fictional_database_explicitly(tmp_path: Path) -> None:
    """Discovery would find a real store; naming the path on the command line cannot."""
    db_path = tmp_path / "world.db"

    server_argv = ("uvx", "--from", "people-context", "people-context-mcp")

    written = write_mcp_config(tmp_path / "mcp.json", server_argv, db_path)

    document = json.loads(written.read_text(encoding="utf-8"))
    server = document["mcpServers"]["people-context"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "people-context", "people-context-mcp", "--db", str(db_path)]


def test_a_with_mcp_request_without_a_configuration_is_refused(tmp_path: Path) -> None:
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT)))

    with pytest.raises(EvalHarnessError, match="needs both mcp_argv and a written MCP client"):
        runner.run(_request(tmp_path, "with_mcp"))


def test_the_child_environment_is_an_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: an inherited PEOPLE_CONTEXT_DB would point the agent at a real store."""
    monkeypatch.setenv("PEOPLE_CONTEXT_DB", str(tmp_path / "real.db"))
    monkeypatch.setenv("EVAL_FAKE_KEY", "value")
    monkeypatch.setenv("EVAL_UNDECLARED", "value")
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT), env_passthrough=["EVAL_FAKE_KEY"]))

    env = json.loads(runner.run(_request(tmp_path, "without_mcp")).answer)["env"]

    assert set(env) - _INTERPRETER_ADDED == {"EVAL_FAKE_KEY"}


def test_an_undeclared_variable_is_simply_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_MISSING_KEY", raising=False)
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT), env_passthrough=["EVAL_MISSING_KEY"]))

    env = json.loads(runner.run(_request(tmp_path, "without_mcp")).answer)["env"]

    assert set(env) - _INTERPRETER_ADDED == set()


def test_a_typo_in_a_placeholder_is_refused_at_construction(tmp_path: Path) -> None:
    """Passed through literally, a typo would produce a confident but meaningless number."""
    config = _config(
        _agent(tmp_path, _ECHO_AGENT),
        argv=[sys.executable, str(tmp_path / "agent.py"), "{system_prompt}", "{prompt}", "{promt}"],
    )

    with pytest.raises(EvalHarnessError, match="is not a whole placeholder"):
        CommandAgentRunner(config)


def test_an_embedded_placeholder_is_refused(tmp_path: Path) -> None:
    config = _config(
        _agent(tmp_path, _ECHO_AGENT),
        argv=[sys.executable, str(tmp_path / "agent.py"), "{system_prompt}", "{prompt}", "--prompt={prompt}"],
    )

    with pytest.raises(EvalHarnessError, match="is not a whole placeholder"):
        CommandAgentRunner(config)


def test_the_mcp_placeholder_is_not_accepted_in_the_base_vector(tmp_path: Path) -> None:
    config = _config(
        _agent(tmp_path, _ECHO_AGENT),
        argv=[sys.executable, str(tmp_path / "agent.py"), "{system_prompt}", "{prompt}", "{mcp_config}"],
    )

    with pytest.raises(EvalHarnessError, match="is not a whole placeholder"):
        CommandAgentRunner(config)


def test_a_failing_agent_refuses_rather_than_scoring_zero(tmp_path: Path) -> None:
    """A broken CLI must not be published as a model that answered badly."""
    runner = CommandAgentRunner(_config(_agent(tmp_path, _FAILING_AGENT)))

    with pytest.raises(EvalHarnessError, match="exit code 3.*boom: model unavailable"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_an_empty_answer_is_refused(tmp_path: Path) -> None:
    runner = CommandAgentRunner(_config(_agent(tmp_path, _SILENT_AGENT)))

    with pytest.raises(EvalHarnessError, match="produced no answer"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_a_hanging_agent_is_timed_out(tmp_path: Path) -> None:
    runner = CommandAgentRunner(_config(_agent(tmp_path, _SLOW_AGENT), timeout_seconds=0.5))

    with pytest.raises(EvalHarnessError, match="timed out after 0.5s"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_a_missing_executable_names_the_command(tmp_path: Path) -> None:
    runner = CommandAgentRunner(
        _config(
            _agent(tmp_path, _ECHO_AGENT),
            argv=["definitely-not-installed-agent", "{system_prompt}", "{prompt}"],
        )
    )

    with pytest.raises(EvalHarnessError, match="cannot start agent command"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_output_beyond_the_cap_is_refused_rather_than_scored_truncated(tmp_path: Path) -> None:
    """A cut answer is not an answer: the missing part may be the part that matched."""
    runner = CommandAgentRunner(_config(_agent(tmp_path, _NOISY_AGENT), max_output_bytes=1024))

    with pytest.raises(EvalHarnessError, match="exceeded the 1024 byte output cap"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_a_runaway_agent_is_killed_before_it_fills_the_disk(tmp_path: Path) -> None:
    """Regression: the cap was applied only after exit, so an output loop was unbounded."""
    runner = CommandAgentRunner(
        _config(_agent(tmp_path, _RUNAWAY_AGENT), max_output_bytes=1024, timeout_seconds=30)
    )

    with pytest.raises(EvalHarnessError, match="exceeded the 1024 byte output cap"):
        runner.run(_request(tmp_path, "without_mcp"))


def test_the_agent_runs_in_the_run_directory_not_the_checkout(tmp_path: Path) -> None:
    """An agent CLI started in the checkout would load the project's own .mcp.json."""
    script = _agent(tmp_path, 'import os\nprint(os.getcwd())\n')
    workdir = tmp_path / "work"
    workdir.mkdir()
    runner = CommandAgentRunner(
        _config(script, argv=[sys.executable, str(script), "{system_prompt}", "{prompt}"]),
    )

    response = runner.run(_request(workdir, "without_mcp"))

    assert Path(response.answer).resolve() == workdir.resolve()


def test_a_vector_without_the_prompt_placeholder_is_refused(tmp_path: Path) -> None:
    """Regression: stdin is DEVNULL, so a vector missing {prompt} asks the agent nothing."""
    with pytest.raises(ValueError, match=r"argv must pass \{prompt\}"):
        _config(_agent(tmp_path, _ECHO_AGENT), argv=[sys.executable, "{system_prompt}"])


def test_a_vector_without_the_system_prompt_placeholder_is_refused(tmp_path: Path) -> None:
    """The report records the system prompt as used; a vector dropping it would lie."""
    with pytest.raises(ValueError, match=r"argv must pass \{system_prompt\}"):
        _config(_agent(tmp_path, _ECHO_AGENT), argv=[sys.executable, "{prompt}"])


def test_mcp_arguments_without_the_config_placeholder_are_refused(tmp_path: Path) -> None:
    """Regression: a with_mcp run could proceed without ever receiving the server."""
    with pytest.raises(ValueError, match=r"mcp_argv must pass \{mcp_config\}"):
        _config(_agent(tmp_path, _ECHO_AGENT), mcp_argv=["--allowedTools", "mcp__people-context"])


_VERSION_AGENT = """
print("2.4.1 (Claude Code)")
"""


def test_the_client_version_is_recorded_when_the_suite_configures_a_probe(tmp_path: Path) -> None:
    """A published score should name the client build that produced it."""
    probe = _agent(tmp_path, _VERSION_AGENT, name="version.py")
    runner = CommandAgentRunner(
        _config(_agent(tmp_path, _ECHO_AGENT), version_argv=[sys.executable, str(probe)])
    )

    assert runner.probe_client_version() == "2.4.1 (Claude Code)"


def test_no_probe_configured_records_nothing(tmp_path: Path) -> None:
    runner = CommandAgentRunner(_config(_agent(tmp_path, _ECHO_AGENT)))

    assert runner.probe_client_version() is None


def test_a_failing_probe_never_fails_the_evaluation(tmp_path: Path) -> None:
    """Recording provenance is worth having, but not worth losing a paid run over."""
    runner = CommandAgentRunner(
        _config(_agent(tmp_path, _ECHO_AGENT), version_argv=["definitely-not-installed-agent", "--version"])
    )

    assert runner.probe_client_version() is None


def test_a_probe_that_exits_non_zero_records_nothing(tmp_path: Path) -> None:
    probe = _agent(tmp_path, _FAILING_AGENT, name="version.py")
    runner = CommandAgentRunner(
        _config(_agent(tmp_path, _ECHO_AGENT), version_argv=[sys.executable, str(probe)])
    )

    assert runner.probe_client_version() is None
