"""The single failure type the harness raises for refused or invalid input."""

from __future__ import annotations


class EvalHarnessError(RuntimeError):
    """Raised when the harness refuses to run or cannot score a run.

    Messages name paths, task ids, and environment variable *names*; they never
    carry environment variable values, so a failure is safe to paste into an
    issue.
    """
