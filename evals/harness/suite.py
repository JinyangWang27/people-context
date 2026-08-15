"""Strict schemas for the evaluation suite: tasks, rubrics, and runner wiring.

Every document the harness reads is validated with ``extra="forbid"`` and
explicit bounds. A suite that has drifted from the schema fails at load time
rather than producing a report that silently skipped a criterion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evals.harness.errors import EvalHarnessError

#: Upper bound on any document the harness loads. The suite is repository-authored,
#: but a bounded read keeps a corrupted or truncated file from becoming a memory issue.
MAX_DOCUMENT_BYTES = 1_048_576

_TASK_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class _StrictModel(BaseModel):
    """Base for every suite document model: forbid unknown keys, forbid mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _CriterionBase(_StrictModel):
    """Fields shared by every rubric criterion."""

    id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=400)
    weight: int = Field(default=1, ge=1, le=100)


class ContainsAllCriterion(_CriterionBase):
    """Passes when the answer contains every listed phrase."""

    kind: Literal["answer_contains_all"]
    values: tuple[str, ...] = Field(min_length=1, max_length=20)


class ContainsNoneCriterion(_CriterionBase):
    """Passes when the answer contains none of the listed phrases."""

    kind: Literal["answer_contains_none"]
    values: tuple[str, ...] = Field(min_length=1, max_length=20)


def _require_compilable(value: str) -> str:
    """Reject a pattern the harness could not apply, at load time rather than mid-run."""
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc
    return value


class MatchesCriterion(_CriterionBase):
    """Passes when the answer matches a case-insensitive regular expression.

    Word-boundary patterns are how the suite separates people whose names are
    prefixes of one another, which plain substring matching cannot do.
    """

    kind: Literal["answer_matches"]
    pattern: str = Field(min_length=1, max_length=400)

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str) -> str:
        return _require_compilable(value)


class LineMatchesCriterion(_CriterionBase):
    """Passes when at least ``min_lines`` individual lines match the expression.

    Line structure is preserved for this kind alone. Some stored preferences are about
    layout — "bullet points, no preamble" is one — and the whitespace collapsing that
    makes the other kinds wrapping-insensitive would erase exactly what is being
    measured, scoring a single-line message with two dashes in it as a bulleted list.
    """

    kind: Literal["answer_lines_match"]
    pattern: str = Field(min_length=1, max_length=400)
    min_lines: int = Field(default=1, ge=1, le=50)

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str) -> str:
        return _require_compilable(value)


Criterion = Annotated[
    ContainsAllCriterion | ContainsNoneCriterion | MatchesCriterion | LineMatchesCriterion,
    Field(discriminator="kind"),
]


class Task(_StrictModel):
    """One fixed question plus the rubric that scores an answer to it."""

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    rubric: tuple[Criterion, ...] = Field(min_length=1, max_length=20)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _TASK_ID.match(value):
            raise ValueError("task ids must be lowercase hyphen-separated slugs")
        return value

    @model_validator(mode="after")
    def _unique_criterion_ids(self) -> Task:
        ids = [criterion.id for criterion in self.rubric]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion ids must be unique within a task")
        return self

    @property
    def possible_weight(self) -> int:
        """Return the total weight an answer to this task can earn."""
        return sum(criterion.weight for criterion in self.rubric)


class StubRunnerConfig(_StrictModel):
    """An offline runner that replays recorded answers; never opens a socket."""

    kind: Literal["stub"]
    model_id: str = Field(min_length=1, max_length=200)
    transcripts: str = Field(min_length=1, max_length=200)


class CommandRunnerConfig(_StrictModel):
    """A runner that invokes an external agent CLI as an argument vector.

    There is no shell, no interpolation into a command string, and no free-form
    argument source: the vector comes from the suite, and only the declared
    placeholders are substituted, each as a whole argument.
    """

    kind: Literal["command"]
    model_id: str = Field(min_length=1, max_length=200)
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    mcp_argv: tuple[str, ...] = Field(default=(), max_length=64)
    mcp_server_argv: tuple[str, ...] = Field(default=(), max_length=64)
    timeout_seconds: float = Field(gt=0, le=3600)
    max_output_bytes: int = Field(ge=1024, le=8_388_608)
    env_passthrough: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("env_passthrough")
    @classmethod
    def _never_forward_store_configuration(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse to hand the agent process any people-context store configuration.

        The evaluated agent must reach exactly the fictional database the harness
        built and named on the server command line. Forwarding ``PEOPLE_CONTEXT_DB``
        or its encryption key would let a developer's real store leak into a run.
        """
        leaked = sorted(name for name in value if name.upper().startswith("PEOPLE_CONTEXT"))
        if leaked:
            raise ValueError("env_passthrough must not forward store configuration: " + ", ".join(leaked))
        return value

    @model_validator(mode="after")
    def _mcp_wiring_is_complete(self) -> CommandRunnerConfig:
        if bool(self.mcp_argv) != bool(self.mcp_server_argv):
            raise ValueError("mcp_argv and mcp_server_argv must be configured together")
        return self

    @model_validator(mode="after")
    def _required_placeholders_are_present(self) -> CommandRunnerConfig:
        """Refuse a vector that could not deliver what the report says it delivered.

        Without ``{prompt}`` the child is handed no task at all, since its stdin is
        ``DEVNULL``; without ``{system_prompt}`` the run is not the one the report
        records; and a ``with_mcp`` vector missing ``{mcp_config}`` would run without
        the server while still being labelled as having had it. Each would produce a
        confidently mislabelled score rather than a failure.
        """
        for placeholder in ("{prompt}", "{system_prompt}"):
            if placeholder not in self.argv:
                raise ValueError(f"argv must pass {placeholder} as a whole argument")
        if self.mcp_argv and "{mcp_config}" not in self.mcp_argv:
            raise ValueError("mcp_argv must pass {mcp_config} as a whole argument")
        return self


RunnerConfig = Annotated[StubRunnerConfig | CommandRunnerConfig, Field(discriminator="kind")]


class Suite(_StrictModel):
    """The complete evaluation definition: world, prompts, tasks, and runners."""

    format: Literal["people-context.eval-suite"]
    version: Literal[1]
    suite_id: str = Field(min_length=1, max_length=64)
    suite_version: str = Field(min_length=1, max_length=32)
    world: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(min_length=1, max_length=4000)
    tasks: tuple[Task, ...] = Field(min_length=1, max_length=50)
    runners: dict[str, RunnerConfig] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _unique_task_ids(self) -> Suite:
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique within a suite")
        return self


class LoadedSuite:
    """A validated suite together with the directory its relative paths resolve against."""

    def __init__(self, suite: Suite, directory: Path) -> None:
        self.suite = suite
        self.directory = directory

    def asset_path(self, relative: str) -> Path:
        """Resolve a suite-relative asset, refusing anything outside the suite directory."""
        candidate = (self.directory / relative).resolve()
        root = self.directory.resolve()
        if candidate != root and root not in candidate.parents:
            raise EvalHarnessError(f"suite asset escapes the suite directory: {relative}")
        if not candidate.is_file():
            raise EvalHarnessError(f"suite asset is not a readable file: {relative}")
        return candidate

    @property
    def world_path(self) -> Path:
        """Return the resolved path of the fictional world fixture."""
        return self.asset_path(self.suite.world)

    def runner_config(self, name: str) -> RunnerConfig:
        """Return one configured runner, or refuse with the names that do exist."""
        try:
            return self.suite.runners[name]
        except KeyError:
            known = ", ".join(sorted(self.suite.runners))
            raise EvalHarnessError(f"unknown runner {name!r}; the suite defines: {known}") from None

    def select_tasks(self, only: tuple[str, ...]) -> tuple[Task, ...]:
        """Return the requested tasks in suite order, refusing unknown ids."""
        if not only:
            return self.suite.tasks
        known = {task.id for task in self.suite.tasks}
        unknown = sorted(set(only) - known)
        if unknown:
            raise EvalHarnessError("unknown task ids: " + ", ".join(unknown))
        wanted = set(only)
        return tuple(task for task in self.suite.tasks if task.id in wanted)


def read_json_document(path: Path) -> object:
    """Read one bounded JSON document, failing closed on size or syntax."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvalHarnessError(f"cannot read {path}: {exc}") from exc
    if size > MAX_DOCUMENT_BYTES:
        raise EvalHarnessError(f"{path} is larger than the {MAX_DOCUMENT_BYTES} byte harness limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvalHarnessError(f"cannot parse {path}: {exc}") from exc


def load_suite(path: Path) -> LoadedSuite:
    """Load and validate the suite document at ``path``."""
    document = read_json_document(path)
    try:
        suite = Suite.model_validate(document)
    except ValidationError as exc:
        raise EvalHarnessError(f"invalid evaluation suite {path}: {exc}") from exc
    return LoadedSuite(suite, path.parent)
