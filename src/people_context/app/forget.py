"""Atomic hard-delete and audit-redaction orchestration."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import (
    PersonNotFoundError,
    RecordNotFoundError,
    audit_mutation,
    require_active_person,
    transactional,
    unit_of_work_for,
)
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.lifecycle import ForgetStoreResult, LifecycleStore, LifecycleTargetNotFoundError
from people_context.ports.repository import PersonReader

_RECORD_TYPES = {"relationship", "affiliation", "fact", "observation", "trait", "interaction", "reminder"}


class ForgetError(Exception):
    """Raised for invalid forget scopes or record targets."""

    def __init__(self, code: str, message: str, **details: str) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class ForgetResult(BaseModel):
    """Confirmation and per-table hard-delete counts."""

    scope: str
    target: str
    deleted: dict[str, int]


class Forget:
    """Validate and execute irreversible person or record deletion."""

    def __init__(
        self,
        people: PersonReader,
        lifecycle: LifecycleStore,
        clock: Clock,
        audit: AuditLog | None = None,
    ) -> None:
        self._people = people
        self._lifecycle = lifecycle
        self._clock = clock
        self._audit = audit or lifecycle.audit_log
        self._uow = unit_of_work_for(lifecycle, self._audit)

    @transactional
    def execute(self, target: str, scope: str) -> ForgetResult:
        """Forget one stored person or one validated record target atomically."""
        if scope == "person":
            if self._people.get(target) is None:
                raise PersonNotFoundError(target)
            store_result = self._lifecycle.forget_person(target)
            self._record_audit(scope, "person", target, target, store_result)
            return ForgetResult(scope=scope, target=target, deleted=store_result.deleted)
        if scope != "record":
            raise ForgetError("invalid_scope", "scope must be 'person' or 'record'", scope=scope)
        entity_type, entity_id = self._parse_record_target(target)
        try:
            store_result = self._lifecycle.forget_record(entity_type, entity_id)
        except LifecycleTargetNotFoundError:
            raise RecordNotFoundError(entity_type, entity_id) from None
        self._record_audit(scope, entity_type, entity_id, target, store_result)
        return ForgetResult(scope=scope, target=target, deleted=store_result.deleted)

    @staticmethod
    def _parse_record_target(target: str) -> tuple[str, str]:
        entity_type, separator, entity_id = target.partition(":")
        if not separator or entity_type not in _RECORD_TYPES or not entity_id:
            raise ForgetError(
                "invalid_target",
                "record target must be a supported entity_type:entity_id",
                target=target,
            )
        return entity_type, entity_id

    def _record_audit(
        self,
        scope: str,
        target_type: str,
        target_id: str,
        audit_target: str,
        store_result: ForgetStoreResult,
    ) -> None:
        tombstone = {
            "scope": scope,
            "target_type": target_type,
            "target_id": target_id,
            "affected_entities": [
                {"entity_type": entity.entity_type, "entity_id": entity.entity_id}
                for entity in store_result.affected_entities
            ],
            "covered_op_ids": store_result.covered_op_ids,
            "covered_transaction_ids": store_result.covered_transaction_ids,
        }
        audit_mutation(
            self._audit,
            self._clock,
            op="forget",
            entity_type=scope,
            entity_id=audit_target,
            payload={"scope": scope, "deleted": store_result.deleted},
            replay_payload=tombstone,
            changed_fields=[],
            op_kind="forget",
            changelog_entity_type=target_type,
            changelog_entity_id=target_id,
            source="agent",
        )


class ForgetPreview(BaseModel):
    """Person deletion preview for confirmation UIs."""

    person_id: str
    canonical_name: str
    deleted: dict[str, int]


class PreviewForget:
    """Return a non-mutating person-scope deletion preview."""

    def __init__(self, people: PersonReader, lifecycle: LifecycleStore) -> None:
        self._people = people
        self._lifecycle = lifecycle

    def execute(self, person_id: str) -> ForgetPreview:
        person = require_active_person(self._people, person_id)
        return ForgetPreview(
            person_id=person.id,
            canonical_name=person.canonical_name,
            deleted=self._lifecycle.preview_person_forget(person.id),
        )
