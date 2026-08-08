"""Shared pytest fixtures."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest

# A POSIX `TZ` string rather than a zoneinfo name, so the fixture needs no tzdata package.
# The POSIX sign is inverted: `+12` is twelve hours west of UTC. Under it a naive timestamp
# read in the host timezone moves twelve hours later, which is enough to reverse an ordering
# and change which records survive a top-N cutoff.
HOST_TIMEZONE_UTC_MINUS_12 = "XYZ+12"


@pytest.fixture
def host_timezone() -> Iterator[object]:
    """Run a test under an explicit host timezone, restoring the process afterwards.

    Reads that compare stored timestamps as instants must not consult the host timezone, and
    the only way to prove that is to run them somewhere that is not UTC.
    """
    previous = os.environ.get("TZ")

    def _use(name: str) -> None:
        if not hasattr(time, "tzset"):  # pragma: no cover - POSIX-only in supported CI
            pytest.skip("the host timezone cannot be changed at runtime on this platform")
        os.environ["TZ"] = name
        time.tzset()

    try:
        yield _use
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()
