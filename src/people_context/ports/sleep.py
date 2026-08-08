"""Sleep port: injectable pause between polls of a long-running local command."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Sleeper(Protocol):
    """Pause the current thread for a number of seconds."""

    def sleep(self, seconds: float) -> None: ...


class SystemSleeper:
    """Concrete sleeper backed by the system clock."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
