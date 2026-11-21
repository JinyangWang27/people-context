"""Resolve the organization a person-to-organization record names, creating it by name if needed."""

from __future__ import annotations

import re

from people_context.app._mutation import OrganizationNotFoundError, audit_mutation, snapshot
from people_context.domain.organization import Organization
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import OrganizationStore

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)


def resolve_organization(
    organizations: OrganizationStore,
    audit: AuditLog,
    clock: Clock,
    org: str,
    *,
    source: str,
    transaction_id: str,
    session: str | None,
    stated_by: str | None,
) -> Organization:
    """Return the organization `org` names: an existing id, or an existing or new one by name.

    An id-shaped value that matches nothing is refused rather than turned into an organization
    literally named after it. A new organization is audited as its own create in the caller's
    transaction. Shared by every record that points at an organization, so employment and
    education resolve the same name to the same row.
    """
    organization = organizations.get(org)
    if organization is None and _ULID_PATTERN.fullmatch(org):
        raise OrganizationNotFoundError(org)
    if organization is None:
        organization = organizations.get_by_normalized_name(normalize_name(org))
    if organization is not None:
        return organization
    organization = Organization(name=org)
    organizations.save(organization)
    audit_mutation(
        audit,
        clock,
        op="create",
        entity_type="organization",
        entity_id=organization.id,
        payload=snapshot(organization),
        source=source,
        transaction_id=transaction_id,
        session=session,
        stated_by=stated_by,
    )
    return organization
