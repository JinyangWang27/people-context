"""Resource budgets for import boundaries that must stay finite.

The import lifecycle predates these limits and keeps working without them: an `ImportBudget`
whose fields are ``None`` is the released, unbounded contract, which is what the MCP tools,
direct application callers, and `pctx init` continue to pass. A budget is something a newer
process boundary chooses for itself.

`CLI_IMPORT_BUDGET` is that choice for the M16 `pctx import` group. Its numbers are ceilings
on work, not a judgement about how much someone may import: a structured export legitimately
holds tens of thousands of narrow rows, so the limits sit far above ordinary use and exist so
that a wrong path, a runaway export, or a staging batch created through an older uncapped
surface cannot become an unbounded read, parse, render, or commit selection.

The extraction limits further down are a different kind of number. They apply to one MCP
`stage_candidates` request that opts into an M17 candidate type, and they are small because
that request carries an agent's distillation of unstructured material rather than rows out of
a structured export. They are conditional on purpose: a request built only from the four
released candidate types keeps the accepted shape it shipped with, so adding candidate types
does not retroactively narrow anybody's working import.

`pctx import stage-candidates` applies those same extraction numbers *unconditionally*, plus a
bound on the bytes of candidate JSON it will read at all. A conditional cap is the right answer
for a released MCP contract that predates it; it is the wrong answer for a brand-new process
boundary, where a path or a pipe typed at a terminal is a much weaker promise about size than an
array an in-process caller already built.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from people_context.app.imports.models import ImportPipelineError

#: Source-file bytes one `pctx import stage` invocation may read.
MAX_CLI_SOURCE_BYTES: Final = 64 * 1024 * 1024

#: Candidates one `pctx import stage` invocation may produce from a single source.
MAX_CLI_STAGED_CANDIDATES: Final = 100_000

#: Persisted reviewable staging payload — staged `source` plus candidate JSON, in UTF-8
#: bytes — that the CLI is willing to stage, review, or commit for one batch.
MAX_CLI_STAGED_PAYLOAD_BYTES: Final = 64 * 1024 * 1024

#: Candidates one `stage_candidates` request carrying an M17 candidate type may submit.
MAX_EXTRACTION_CANDIDATES: Final = 500

#: Characters the normalized `source` label of such a request may carry. `StageCandidates`
#: copies that label into every staged row and every later provenance record, so an unbounded
#: one is a transcript side channel as much as it is a resource problem.
MAX_EXTRACTION_SOURCE_CHARS: Final = 128

#: UTF-8 bytes of the canonical serialization of such a request's complete candidate array.
MAX_EXTRACTION_PAYLOAD_BYTES: Final = 1024 * 1024

#: UTF-8 bytes any single string in such a request may carry — including strings on the legacy
#: candidate types, so a mixed batch cannot smuggle a transcript through a `fact` value.
MAX_EXTRACTION_STRING_BYTES: Final = 8 * 1024

#: UTF-8 bytes of candidate JSON one `pctx import stage-candidates` invocation may read. This is
#: a read bound rather than a request bound: it is spent before the input is decoded or parsed, so
#: an oversized file or a stdin stream that never ends is refused instead of being buffered whole.
MAX_CLI_CANDIDATE_JSON_BYTES: Final = 1024 * 1024

SOURCE_TOO_LARGE = "source_too_large"
TOO_MANY_CANDIDATES = "too_many_candidates"
STAGED_PAYLOAD_TOO_LARGE = "staged_payload_too_large"
BATCH_TOO_LARGE_FOR_CLI = "batch_too_large_for_cli"
SOURCE_LABEL_TOO_LONG = "source_label_too_long"
CANDIDATE_PAYLOAD_TOO_LARGE = "candidate_payload_too_large"
CANDIDATE_STRING_TOO_LONG = "candidate_string_too_long"
CANDIDATE_STRING_NOT_ENCODABLE = "candidate_string_not_encodable"
CANDIDATE_INPUT_TOO_LARGE = "candidate_input_too_large"
INVALID_CANDIDATE_JSON = "invalid_candidate_json"


@dataclass(frozen=True)
class ImportBudget:
    """The resource ceilings one import boundary applies; ``None`` means unbounded."""

    max_source_bytes: int | None = None
    max_candidates: int | None = None
    max_staged_payload_bytes: int | None = None


#: The released contract of every pre-M16 caller, stated explicitly rather than implied.
UNBOUNDED_IMPORT_BUDGET: Final = ImportBudget()

#: The budget of the `pctx import` process boundary.
CLI_IMPORT_BUDGET: Final = ImportBudget(
    max_source_bytes=MAX_CLI_SOURCE_BYTES,
    max_candidates=MAX_CLI_STAGED_CANDIDATES,
    max_staged_payload_bytes=MAX_CLI_STAGED_PAYLOAD_BYTES,
)


def resource_limit_error(code: str, message: str, **details: Any) -> ImportPipelineError:
    """Return a refusal that names the limit and never the rejected payload.

    Every caller of this helper has just rejected untrusted content, so the message and the
    details are restricted to numbers the operator needs in order to split the input.
    """
    return ImportPipelineError(code, message, **details)


def enforce_extraction_request_limits(source: str, candidates: list[Any]) -> None:
    """Bound one staging request that opts into an M17 candidate type, before it is staged.

    Everything here runs on the raw request, ahead of model validation and well ahead of any
    durable row, because the point is to refuse an oversized payload rather than to parse one
    first. The checks run cheapest-first — a count, then a label length, then a walk that stops
    at the first oversized string — so the serialization that measures the whole array is only
    reached by a request that has already survived the narrower limits.
    """
    if len(candidates) > MAX_EXTRACTION_CANDIDATES:
        raise resource_limit_error(
            TOO_MANY_CANDIDATES,
            f"an extraction request stages at most {MAX_EXTRACTION_CANDIDATES} candidates",
            limit=MAX_EXTRACTION_CANDIDATES,
        )
    if len(source) > MAX_EXTRACTION_SOURCE_CHARS:
        raise resource_limit_error(
            SOURCE_LABEL_TOO_LONG,
            f"an extraction source label is at most {MAX_EXTRACTION_SOURCE_CHARS} characters",
            limit=MAX_EXTRACTION_SOURCE_CHARS,
        )
    for candidate in candidates:
        _reject_oversized_strings(candidate)
    payload_bytes = len(_canonical_payload(candidates).encode("utf-8"))
    if payload_bytes > MAX_EXTRACTION_PAYLOAD_BYTES:
        raise resource_limit_error(
            CANDIDATE_PAYLOAD_TOO_LARGE,
            f"an extraction request carries at most {MAX_EXTRACTION_PAYLOAD_BYTES} bytes of candidate JSON",
            limit=MAX_EXTRACTION_PAYLOAD_BYTES,
        )


def _canonical_payload(candidates: list[Any]) -> str:
    """Serialize the complete candidate array deterministically for measurement only."""
    return json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _reject_oversized_strings(candidate: Any) -> None:
    """Refuse the first string anywhere in one candidate that exceeds the all-string limit.

    The walk is iterative rather than recursive so that a deeply nested request cannot turn a
    size check into a stack overflow, and it covers keys as well as values: an oversized key
    would otherwise reach validation only to be refused there, having already been measured
    into the payload.
    """
    pending: list[Any] = [candidate]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            # A JSON escape may decode to an unpaired surrogate, which is a `str` that has no
            # UTF-8 encoding at all. Measuring it would raise where a refusal belongs: the value
            # cannot be stored, so it is rejected here rather than crashing the size check.
            try:
                measured = len(current.encode("utf-8"))
            except UnicodeEncodeError:
                raise resource_limit_error(
                    CANDIDATE_STRING_NOT_ENCODABLE,
                    "an extraction candidate string must be encodable as UTF-8",
                ) from None
            if measured > MAX_EXTRACTION_STRING_BYTES:
                raise resource_limit_error(
                    CANDIDATE_STRING_TOO_LONG,
                    f"an extraction candidate string is at most {MAX_EXTRACTION_STRING_BYTES} bytes",
                    limit=MAX_EXTRACTION_STRING_BYTES,
                )
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
