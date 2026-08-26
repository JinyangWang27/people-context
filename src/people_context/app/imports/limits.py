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
"""

from __future__ import annotations

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

SOURCE_TOO_LARGE = "source_too_large"
TOO_MANY_CANDIDATES = "too_many_candidates"
STAGED_PAYLOAD_TOO_LARGE = "staged_payload_too_large"
BATCH_TOO_LARGE_FOR_CLI = "batch_too_large_for_cli"


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
