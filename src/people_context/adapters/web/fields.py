"""What an edit form offers for each staged candidate type (M30.3).

The form is generated from the candidate type's declared field list rather than from the keys one
candidate happens to carry: an optional left unset at staging — an alias's `lang`, a fact's
`valid_to` — still gets an input, which is the whole point of editing. Walking `model_fields`
also means a field added to a persisted model appears in the form without anything here changing,
and `tests/adapters/web/test_browse_fields.py` fails if one appears that this cannot classify.

It lives in the web adapter because it is presentation: a control per field, and the candidate
types a selector may offer. The rules it projects belong to `app/imports` and `domain/`, and
nothing here re-states one — a selector is narrowed to exactly the types
`_check_batch_references` accepts, so a control cannot express a reference the use case refuses.
"""

from __future__ import annotations

import enum
import typing
from collections.abc import Iterator
from datetime import date
from typing import Any

from people_context.app.imports.amendment import IMMUTABLE_PATCH_FIELDS
from people_context.domain.import_provenance import (
    EVIDENCE_CAPABLE_STAGED_TYPES,
    GROUP_CAPABLE_STAGED_TYPES,
    STAGED_DURABLE_REFERENCE_FIELDS,
    STAGED_EVIDENCE_REFERENCE_FIELDS,
    STAGED_GROUP_REFERENCE_FIELDS,
    STAGED_REFERENCE_FIELDS,
    STAGED_REFERENCE_LIST_FIELDS,
)
from people_context.domain.staged_candidate import STAGED_CANDIDATE_MODELS, StagedAlias

#: The one person-candidate field the generic form never offers.
#:
#: It records what identity matching concluded, and the only patch allowed to set it is a choice
#: the matcher itself produced — which is the ambiguity picker, not a text box.
_PICKER_FIELD = "matched_person_id"

#: The candidate types a reference field may name, by field. Anything else is a person.
_REFERENCE_TARGETS: dict[str, frozenset[str]] = {
    **{field: EVIDENCE_CAPABLE_STAGED_TYPES for field in STAGED_EVIDENCE_REFERENCE_FIELDS},
    **{field: GROUP_CAPABLE_STAGED_TYPES for field in STAGED_GROUP_REFERENCE_FIELDS},
}
_PERSON_TARGETS: frozenset[str] = frozenset({"person"})


def _targets(field_name: str) -> list[str]:
    return sorted(_REFERENCE_TARGETS.get(field_name, _PERSON_TARGETS))


def _declared_types(annotation: Any) -> Iterator[type]:
    """Yield the concrete types one field annotation admits, besides `None`.

    `Optional[Annotated[float, Field(...)]]` and a bare `Sensitivity` both have to answer the
    same question — what is this field, really — so unions and `Annotated` are both unwrapped
    rather than pattern-matched one nesting at a time.
    """
    if isinstance(annotation, type):
        if annotation is not type(None):
            yield annotation
        return
    for argument in typing.get_args(annotation):
        yield from _declared_types(argument)


def _enum_options(annotation: Any) -> list[str] | None:
    """Return the values of the enum this annotation resolves to, if it is one."""
    for declared in _declared_types(annotation):
        if issubclass(declared, enum.Enum):
            return [str(member.value) for member in declared]
    return None


def _scalar_type(annotation: Any) -> type | None:
    """Return the first concrete type this annotation admits, if any."""
    return next(_declared_types(annotation), None)


def _descriptor(field_name: str, annotation: Any) -> dict[str, Any]:
    """Return the control one declared field is edited with."""
    if field_name == "aliases":
        return {"name": field_name, "control": "alias_list"}
    if field_name in STAGED_DURABLE_REFERENCE_FIELDS:
        # Durable records outside this batch. Offering a picker would mean inventing a record
        # search; the form shows the stored ids and the `pctx import amend` command instead.
        return {"name": field_name, "control": "readonly"}
    if field_name in STAGED_REFERENCE_LIST_FIELDS:
        return {"name": field_name, "control": "candidate_multiselect", "targets": _targets(field_name)}
    if field_name in STAGED_REFERENCE_FIELDS:
        return {"name": field_name, "control": "candidate_select", "targets": _targets(field_name)}
    options = _enum_options(annotation)
    if options is not None:
        return {"name": field_name, "control": "select", "options": options}
    scalar = _scalar_type(annotation)
    if scalar is float:
        return {"name": field_name, "control": "number", "min": 0.0, "max": 1.0, "step": 0.01}
    if scalar is date:
        return {"name": field_name, "control": "date"}
    # Everything else, `datetime` included, is text carrying the staged string verbatim.
    # `datetime-local` has no timezone, so seeding one with a stored `...T09:00:00Z` and reading
    # it back would silently rewrite an offset the reviewer never touched.
    return {"name": field_name, "control": "text"}


def _fields(model: Any, *, skip: frozenset[str]) -> list[dict[str, Any]]:
    return [
        _descriptor(field_name, field.annotation)
        for field_name, field in model.model_fields.items()
        if field_name not in skip
    ]


#: The editable fields of each staged candidate type, in the order the model declares them.
CANDIDATE_FIELDS: dict[str, list[dict[str, Any]]] = {
    kind: _fields(model, skip=IMMUTABLE_PATCH_FIELDS | {_PICKER_FIELD})
    for kind, model in STAGED_CANDIDATE_MODELS.items()
}

#: The fields of one alias row inside a person candidate's `aliases` list.
ALIAS_FIELDS: list[dict[str, Any]] = _fields(StagedAlias, skip=frozenset())
