"""Reproducible evaluation harness for people-context.

The harness answers one question with fixed prompts and deterministic scoring:
does an agent that can read a people-context store answer questions about the
people in it better than the same agent without the store?

Three properties keep it honest and safe to run:

- it materializes its own fictional world into a throwaway database and refuses
  to touch the database the local configuration resolves to;
- it never reads an API key from a file, a flag, or the suite definition — only
  from an explicitly declared process environment variable;
- scoring is textual and rule-based, so the same transcript always produces the
  same score and no model judges another model.
"""

from __future__ import annotations

#: Version of this harness, recorded in every report so a published number names
#: the code that produced it. Bump on any change to prompts, scoring, or reports.
HARNESS_VERSION = "1.0.0"

#: Stable machine identity of the report document. Additive under the same rules
#: as the M12 CLI JSON promise: new keys may appear, existing keys keep meaning.
REPORT_FORMAT = "people-context.eval-report"
REPORT_VERSION = 1

#: The two conditions every task is run under, in report order.
CONDITIONS: tuple[str, ...] = ("with_mcp", "without_mcp")

__all__ = ["CONDITIONS", "HARNESS_VERSION", "REPORT_FORMAT", "REPORT_VERSION"]
