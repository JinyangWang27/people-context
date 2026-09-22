"""Narrow ports for destructive forget operations and previews."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from people_context.ports.lifecycle import ForgetStoreResult


@runtime_checkable
class ForgetStore(Protocol):
    """Persist destructive person or record forget operations atomically."""

    def forget_person(self, person_id: str) -> ForgetStoreResult: ...

    def forget_record(self, entity_type: str, entity_id: str) -> ForgetStoreResult: ...


@runtime_checkable
class ForgetPreviewStore(Protocol):
    """Count person-forget effects without mutation."""

    def preview_person_forget(self, person_id: str) -> dict[str, int]: ...
