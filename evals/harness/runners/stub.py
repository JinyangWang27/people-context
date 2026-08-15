"""An offline runner that replays recorded answers.

This is what makes the harness testable and what a contributor runs first: it
starts no process, opens no socket, and needs no API key, so a dry run proves
the fixture, prompts, scoring, and report plumbing work before anyone spends a
token on a real model.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentResponse
from evals.harness.suite import read_json_document


class StubTranscript(BaseModel):
    """One recorded answer for one task under one condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=64)
    condition: str = Field(min_length=1, max_length=32)
    answer: str = Field(min_length=1, max_length=8000)


class StubTranscripts(BaseModel):
    """The recorded transcript document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str = Field(pattern=r"^people-context\.eval-transcripts$")
    version: int = Field(ge=1, le=1)
    description: str = Field(min_length=1, max_length=1000)
    transcripts: tuple[StubTranscript, ...] = Field(min_length=1, max_length=200)


class StubAgentRunner:
    """Replays recorded answers; refuses any request it has no transcript for."""

    kind = "stub"

    def __init__(self, answers: dict[tuple[str, str], str], model_id: str) -> None:
        self._answers = answers
        self.model_id = model_id

    @classmethod
    def from_path(cls, path: Path, model_id: str) -> StubAgentRunner:
        """Load a validated transcript document."""
        try:
            document = StubTranscripts.model_validate(read_json_document(path))
        except ValidationError as exc:
            raise EvalHarnessError(f"invalid stub transcripts {path}: {exc}") from exc
        answers: dict[tuple[str, str], str] = {}
        for transcript in document.transcripts:
            key = (transcript.task_id, transcript.condition)
            if key in answers:
                raise EvalHarnessError(f"duplicate stub transcript for {key[0]} / {key[1]}")
            answers[key] = transcript.answer
        return cls(answers, model_id)

    def run(self, request: AgentRequest) -> AgentResponse:
        """Return the recorded answer, or refuse rather than score an empty one."""
        try:
            answer = self._answers[(request.task_id, request.condition)]
        except KeyError:
            raise EvalHarnessError(
                f"no stub transcript recorded for task {request.task_id} under {request.condition}"
            ) from None
        return AgentResponse(answer=answer, model_id=self.model_id)
