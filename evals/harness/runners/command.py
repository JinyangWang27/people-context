"""A runner that invokes an external agent CLI as an argument vector.

Everything about this path is deliberately narrow, because it is the only place
the harness starts a process:

- the vector comes from the suite, never from a task prompt or a model;
- arguments are substituted whole, so a prompt can never become extra flags;
- ``shell=False``, so no argument is ever interpreted by a shell;
- the child environment is an allowlist, so an API key is forwarded only when the
  suite names the variable and never appears in a config file or the report;
- output is written to a temporary file and read back under a byte cap, so a
  runaway agent cannot exhaust harness memory.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import IO

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentResponse
from evals.harness.suite import CommandRunnerConfig

#: Placeholders substituted as whole arguments, and where each one is allowed.
_BASE_PLACEHOLDERS = frozenset({"{system_prompt}", "{prompt}", "{model}"})
_MCP_PLACEHOLDERS = frozenset({"{mcp_config}"})

#: Bytes of child stderr kept for a failure message. Enough to identify the
#: failure, small enough that a report or log stays readable.
_STDERR_EXCERPT_BYTES = 2048

_BRACED = re.compile(r"[{}]")


class CommandAgentRunner:
    """Runs one external agent command per request."""

    kind = "command"

    def __init__(self, config: CommandRunnerConfig) -> None:
        _reject_unknown_placeholders(config.argv, _BASE_PLACEHOLDERS)
        _reject_unknown_placeholders(config.mcp_argv, _BASE_PLACEHOLDERS | _MCP_PLACEHOLDERS)
        self._config = config
        self.model_id = config.model_id

    def run(self, request: AgentRequest) -> AgentResponse:
        """Invoke the configured agent and return its final answer."""
        argv = self._argv(request)
        env = _child_environment(self._config.env_passthrough)
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                completed = subprocess.run(
                    argv,
                    shell=False,
                    cwd=str(request.working_directory),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    timeout=self._config.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise EvalHarnessError(
                    f"agent command timed out after {self._config.timeout_seconds}s "
                    f"on task {request.task_id} ({request.condition})"
                ) from exc
            except OSError as exc:
                raise EvalHarnessError(f"cannot start agent command {argv[0]!r}: {exc}") from exc
            stdout, truncated = _read_capped(out, self._config.max_output_bytes)
            if completed.returncode != 0:
                stderr, _ = _read_capped(err, _STDERR_EXCERPT_BYTES)
                raise EvalHarnessError(
                    f"agent command failed with exit code {completed.returncode} "
                    f"on task {request.task_id} ({request.condition}): {stderr.strip()}"
                )
        answer = stdout.strip()
        if not answer:
            raise EvalHarnessError(
                f"agent command produced no answer on task {request.task_id} ({request.condition})"
            )
        return AgentResponse(answer=answer, model_id=self.model_id, truncated=truncated)

    def _argv(self, request: AgentRequest) -> list[str]:
        """Build the vector for one request, substituting whole arguments only."""
        substitutions = {
            "{system_prompt}": request.system_prompt,
            "{prompt}": request.prompt,
            "{model}": self._config.model_id,
        }
        argv = [substitutions.get(argument, argument) for argument in self._config.argv]
        if request.condition != "with_mcp":
            return argv
        if request.mcp_config_path is None or not self._config.mcp_argv:
            raise EvalHarnessError(
                "the with_mcp condition needs both mcp_argv and a written MCP client "
                f"configuration; runner {self._config.model_id!r} cannot be measured with one"
            )
        substitutions["{mcp_config}"] = str(request.mcp_config_path)
        argv.extend(substitutions.get(argument, argument) for argument in self._config.mcp_argv)
        return argv


def _reject_unknown_placeholders(argv: tuple[str, ...], allowed: frozenset[str]) -> None:
    """Refuse a vector containing a brace that is not an allowed whole placeholder.

    A typo such as ``--prompt={promt}`` would otherwise be passed through literally
    and produce a confidently wrong measurement instead of a failure.
    """
    for argument in argv:
        if argument in allowed:
            continue
        if _BRACED.search(argument):
            expected = ", ".join(sorted(allowed))
            raise EvalHarnessError(
                f"argument {argument!r} is not a whole placeholder; expected one of: {expected}"
            )


def _child_environment(passthrough: tuple[str, ...]) -> dict[str, str]:
    """Build the child environment from an allowlist of currently-set variables.

    Values are read from this process only. Names the suite lists but the
    environment does not define are simply absent, so a missing API key surfaces
    as the agent CLI's own authentication error rather than a harness guess.
    """
    return {name: os.environ[name] for name in passthrough if name in os.environ}


def _read_capped(stream: IO[bytes], limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` bytes from a captured stream, reporting truncation."""
    stream.seek(0)
    payload = stream.read(limit + 1)
    truncated = len(payload) > limit
    return payload[:limit].decode("utf-8", errors="replace"), truncated
