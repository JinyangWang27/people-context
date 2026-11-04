"""A runner that invokes an external agent CLI as an argument vector.

Everything about this path is deliberately narrow, because it is the only place
the harness starts a process:

- the vector comes from the suite, never from a task prompt or a model;
- arguments are substituted whole, so a prompt can never become extra flags;
- ``shell=False``, so no argument is ever interpreted by a shell;
- the child environment is an allowlist, so an API key is forwarded only when the
  suite names the variable and never appears in a config file or the report;
- output is captured to a temporary file whose size is checked while the child
  runs, so an agent that will not stop producing is killed rather than allowed to
  fill the disk, and nothing unbounded is ever read into memory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from typing import IO

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentResponse
from evals.harness.suite import CommandRunnerConfig

#: Placeholders substituted as whole arguments, and where each one is allowed.
_BASE_PLACEHOLDERS = frozenset({"{system_prompt}", "{prompt}", "{model}", "{skills_plugin}"})
_MCP_PLACEHOLDERS = frozenset({"{mcp_config}"})

#: Bytes of child stderr kept for a failure message. Enough to identify the
#: failure, small enough that a report or log stays readable.
_STDERR_EXCERPT_BYTES = 2048

#: How often the captured size and the deadline are checked while the child runs.
_POLL_SECONDS = 0.1

#: A version probe that has not answered in this long is not worth waiting for.
_VERSION_PROBE_SECONDS = 30.0

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
        limit = self._config.max_output_bytes
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                process = subprocess.Popen(
                    argv,
                    shell=False,
                    cwd=str(request.working_directory),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    close_fds=True,
                )
            except OSError as exc:
                raise EvalHarnessError(f"cannot start agent command {argv[0]!r}: {exc}") from exc
            returncode = self._supervise(process, out, err, request)
            if _captured_bytes(out) > limit or _captured_bytes(err) > limit:
                # A process that overproduced faster than the poll interval reaches
                # here. Both streams are checked, on both paths: a small valid answer
                # beside a large stderr burst still spends the same disk.
                raise EvalHarnessError(
                    f"agent command exceeded the {limit} byte output cap "
                    f"on task {request.task_id} ({request.condition})"
                )
            stdout = _read_exactly(out, limit)
            if returncode != 0:
                excerpt = _read_exactly(err, _STDERR_EXCERPT_BYTES).strip()
                raise EvalHarnessError(
                    f"agent command failed with exit code {returncode} "
                    f"on task {request.task_id} ({request.condition}): {excerpt}"
                )
        tool_calls: tuple[dict[str, object], ...] | None = None
        usage: dict[str, object] | None = None
        if self._config.output_format == "stream-json":
            answer, tool_calls, usage = _parse_event_stream(stdout, request)
        else:
            answer = stdout.strip()
        if not answer:
            raise EvalHarnessError(
                f"agent command produced no answer on task {request.task_id} ({request.condition})"
            )
        return AgentResponse(answer=answer, model_id=self.model_id, tool_calls=tool_calls, usage=usage)

    def probe_client_version(self) -> str | None:
        """Return the agent client's own version string, or ``None``.

        A published number should name the client that produced it: the same model
        through a different CLI build can see different built-in prompts and MCP
        handling. The probe is best-effort by design — it must never turn a working
        evaluation into a failed one — so any error simply records nothing.
        """
        if not self._config.version_argv:
            return None
        try:
            completed = subprocess.run(
                list(self._config.version_argv),
                shell=False,
                env=_child_environment(self._config.env_passthrough),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_VERSION_PROBE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        first_line = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
        return first_line[0][:200] if first_line else None

    def _supervise(
        self,
        process: subprocess.Popen[bytes],
        out: IO[bytes],
        err: IO[bytes],
        request: AgentRequest,
    ) -> int:
        """Wait for the child, enforcing the deadline and the output cap as it runs.

        Checking the captured size only after exit would bound memory but not disk: an
        agent stuck in an output loop could fill the temporary filesystem before the
        timeout fired. Exceeding the cap is a refusal rather than a truncation, because
        an answer the harness had to cut is not an answer worth scoring.
        """
        deadline = time.monotonic() + self._config.timeout_seconds
        limit = self._config.max_output_bytes
        while True:
            try:
                return process.wait(timeout=_POLL_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            if _captured_bytes(out) > limit or _captured_bytes(err) > limit:
                _terminate(process)
                raise EvalHarnessError(
                    f"agent command exceeded the {limit} byte output cap "
                    f"on task {request.task_id} ({request.condition})"
                )
            if time.monotonic() >= deadline:
                _terminate(process)
                raise EvalHarnessError(
                    f"agent command timed out after {self._config.timeout_seconds}s "
                    f"on task {request.task_id} ({request.condition})"
                )

    def _argv(self, request: AgentRequest) -> list[str]:
        """Build the vector for one request, substituting whole arguments only."""
        substitutions = {
            "{system_prompt}": request.system_prompt,
            "{prompt}": request.prompt,
            "{model}": self._config.model_id,
        }
        if "{skills_plugin}" in self._config.argv or "{skills_plugin}" in self._config.mcp_argv:
            if request.skills_plugin is None:
                raise EvalHarnessError("the runner passes {skills_plugin} but no skills plugin was staged")
            substitutions["{skills_plugin}"] = str(request.skills_plugin)
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


def _parse_event_stream(
    stdout: str, request: AgentRequest
) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
    """Return the final answer, every tool call, and the usage accounting of a ``stream-json`` stream.

    Fails closed: a line that is not JSON, a missing ``result`` event, or an error result is
    a refusal rather than an answer, because a transcript the harness could not read is not
    one a reviewer should be asked to judge.
    """
    where = f"task {request.task_id} ({request.condition})"
    tool_calls: list[dict[str, object]] = []
    tool_names: dict[object, str] = {}
    result_chars = {"mcp": 0, "skill": 0, "other": 0}
    request_ids: list[object] = []
    first_input: int | None = None
    result: dict[str, object] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalHarnessError(f"agent event stream is not JSON lines on {where}: {exc}") from None
        if not isinstance(event, dict):
            raise EvalHarnessError(f"agent event stream holds a non-object event on {where}")
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        if event.get("type") == "assistant" and isinstance(message, dict):
            # One API response arrives as several events sharing a message id; count it once.
            if message.get("id") not in request_ids:
                request_ids.append(message.get("id"))
                if first_input is None:
                    first_input = _prompt_tokens(message.get("usage"))
            for block in blocks:
                if block.get("type") == "tool_use":
                    tool_calls.append({"name": block.get("name"), "input": block.get("input")})
                    tool_names[block.get("id")] = str(block.get("name"))
        elif event.get("type") == "user":
            for block in blocks:
                if block.get("type") == "tool_result":
                    name = tool_names.get(block.get("tool_use_id"), "")
                    bucket = "mcp" if name.startswith("mcp__") else "skill" if name == "Skill" else "other"
                    result_chars[bucket] += _text_length(block.get("content"))
        elif event.get("type") == "result":
            result = event
    if result is None:
        raise EvalHarnessError(f"agent event stream ended without a result on {where}")
    answer = result.get("result")
    if result.get("is_error") or not isinstance(answer, str):
        raise EvalHarnessError(f"agent event stream reported an error result on {where}")
    totals = result.get("usage")
    if not isinstance(totals, dict):
        totals = {}
    usage: dict[str, object] = {
        "api_requests": len(request_ids),
        "num_turns": result.get("num_turns"),
        "first_request_input_tokens": first_input,
        **{key: totals.get(key) for key in _USAGE_KEYS},
        "total_cost_usd": result.get("total_cost_usd"),
        "tool_result_chars": result_chars,
    }
    return answer.strip(), tuple(tool_calls), usage


#: Token counters copied from the client's final ``result`` event, which sums every API request.
_USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _prompt_tokens(usage: object) -> int | None:
    """Return the whole prompt one API request sent: uncached, cache-written, and cache-read tokens."""
    if not isinstance(usage, dict):
        return None
    counts = [usage.get(key) for key in _USAGE_KEYS[:3]]
    return sum(count for count in counts if isinstance(count, int))


def _text_length(content: object) -> int:
    """Return the characters a tool result put back into the model's context."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(part.get("text", ""))) for part in content if isinstance(part, dict))
    return 0


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


def _captured_bytes(stream: IO[bytes]) -> int:
    """Return how much the child has written so far, without reading it."""
    return os.fstat(stream.fileno()).st_size


def _read_exactly(stream: IO[bytes], limit: int) -> str:
    """Read at most ``limit`` bytes from a captured stream."""
    stream.seek(0)
    return stream.read(limit).decode("utf-8", errors="replace")


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Stop a child that broke its contract, escalating if it ignores the signal."""
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
