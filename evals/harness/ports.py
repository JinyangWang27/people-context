"""The single port the harness depends on: something that can answer a prompt.

Keeping the agent behind a narrow ``Protocol`` is what lets the same suite run
offline against recorded transcripts and online against a real model CLI without
the scoring or reporting code knowing which one produced the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentRequest:
    """One prompt, under one condition, for one task."""

    task_id: str
    condition: str
    system_prompt: str
    prompt: str
    #: Path to a written MCP client configuration, or ``None`` in the
    #: ``without_mcp`` condition where the agent has no people-context access.
    mcp_config_path: Path | None
    #: Directory the agent process runs in. It is the run's own scratch directory,
    #: never the checkout, so an agent CLI cannot pick up repository-level
    #: configuration such as a project ``.mcp.json`` and quietly change conditions.
    working_directory: Path


@dataclass(frozen=True)
class AgentResponse:
    """The agent's final answer, plus what produced it."""

    answer: str
    model_id: str


@runtime_checkable
class AgentRunner(Protocol):
    """Produces one answer per request."""

    #: Stable identifier of the runner implementation, recorded in the report.
    kind: str
    #: Identifier of the model the runner speaks to, recorded in the report.
    model_id: str

    def run(self, request: AgentRequest) -> AgentResponse: ...
