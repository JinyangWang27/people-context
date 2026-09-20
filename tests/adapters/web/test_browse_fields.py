"""The edit form's field descriptors against the models they project (M30.3).

The point of generating the form from `model_fields` is that a persisted model and the form
cannot drift. These tests fail when they do — a new field, a widened enum, or a reference field
whose accepted types moved.
"""

from __future__ import annotations

import enum
import typing
from typing import Any

import pytest

from people_context.adapters.web.fields import ALIAS_FIELDS, CANDIDATE_FIELDS
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

_CONTROLS = frozenset(
    {"text", "date", "number", "select", "alias_list", "readonly", "candidate_select", "candidate_multiselect"}
)


def _declared_enum(annotation: Any) -> type[enum.Enum] | None:
    """Return the enum this field annotation admits, unwrapping unions and `Annotated`."""
    if isinstance(annotation, type):
        return annotation if issubclass(annotation, enum.Enum) else None
    for argument in typing.get_args(annotation):
        found = _declared_enum(argument)
        if found is not None:
            return found
    return None


def test_every_staged_candidate_type_has_a_form() -> None:
    assert set(CANDIDATE_FIELDS) == set(STAGED_CANDIDATE_MODELS)


@pytest.mark.parametrize("kind", sorted(STAGED_CANDIDATE_MODELS))
def test_the_form_offers_exactly_the_patchable_fields(kind: str) -> None:
    """Every declared field except the computed match outcome, which the picker owns."""
    declared = list(STAGED_CANDIDATE_MODELS[kind].model_fields)
    expected = [
        name for name in declared if name not in IMMUTABLE_PATCH_FIELDS and name != "matched_person_id"
    ]
    assert [field["name"] for field in CANDIDATE_FIELDS[kind]] == expected


@pytest.mark.parametrize("kind", sorted(STAGED_CANDIDATE_MODELS))
def test_every_control_is_one_the_page_knows(kind: str) -> None:
    assert {field["control"] for field in CANDIDATE_FIELDS[kind]} <= _CONTROLS


@pytest.mark.parametrize("kind", sorted(STAGED_CANDIDATE_MODELS))
def test_an_enum_field_offers_exactly_its_vocabulary(kind: str) -> None:
    model = STAGED_CANDIDATE_MODELS[kind]
    for field in CANDIDATE_FIELDS[kind]:
        declared = _declared_enum(model.model_fields[field["name"]].annotation)
        if declared is None:
            assert field["control"] != "select", field
            continue
        assert field["control"] == "select", field
        assert field["options"] == [str(member.value) for member in declared]


@pytest.mark.parametrize("kind", sorted(STAGED_CANDIDATE_MODELS))
def test_a_selector_offers_only_the_types_the_use_case_resolves(kind: str) -> None:
    """The targets are `_check_batch_references`'s own three namespaces, so a control cannot
    express a reference the amendment would refuse."""
    for field in CANDIDATE_FIELDS[kind]:
        name = field["name"]
        if name in STAGED_EVIDENCE_REFERENCE_FIELDS:
            expected = EVIDENCE_CAPABLE_STAGED_TYPES
        elif name in STAGED_GROUP_REFERENCE_FIELDS:
            expected = GROUP_CAPABLE_STAGED_TYPES
        elif name in STAGED_REFERENCE_FIELDS or name in STAGED_REFERENCE_LIST_FIELDS:
            expected = frozenset({"person"})
        else:
            assert "targets" not in field, field
            continue
        assert field["control"] in {"candidate_select", "candidate_multiselect"}, field
        assert set(field["targets"]) == set(expected)
        assert set(field["targets"]) <= set(STAGED_CANDIDATE_MODELS)


def test_durable_evidence_ids_are_read_only() -> None:
    """They name records outside the batch; the form shows the command rather than a search."""
    trait = {field["name"]: field for field in CANDIDATE_FIELDS["trait"]}
    for name in STAGED_DURABLE_REFERENCE_FIELDS:
        assert trait[name]["control"] == "readonly"


def test_aliases_are_an_add_remove_list_of_every_alias_field() -> None:
    person = {field["name"]: field for field in CANDIDATE_FIELDS["person"]}
    assert person["aliases"]["control"] == "alias_list"
    assert [field["name"] for field in ALIAS_FIELDS] == list(StagedAlias.model_fields)
    assert {field["control"] for field in ALIAS_FIELDS} <= _CONTROLS


def test_confidence_is_a_bounded_number_whether_or_not_it_is_optional() -> None:
    """`Confidence` is `Annotated[float, ...]`, optional on some types and required on others."""
    for kind in ("trait", "fact", "relationship", "affiliation", "group_membership"):
        field = {entry["name"]: entry for entry in CANDIDATE_FIELDS[kind]}["confidence"]
        assert field == {"name": "confidence", "control": "number", "min": 0.0, "max": 1.0, "step": 0.01}


def test_a_datetime_field_keeps_its_stored_string_rather_than_a_local_picker() -> None:
    """`datetime-local` has no timezone; seeding one with a stored offset would rewrite it."""
    interaction = {field["name"]: field for field in CANDIDATE_FIELDS["interaction"]}
    assert interaction["date"]["control"] == "text"
    observation = {field["name"]: field for field in CANDIDATE_FIELDS["observation"]}
    assert observation["observed_at"]["control"] == "text"
    fact = {field["name"]: field for field in CANDIDATE_FIELDS["fact"]}
    assert fact["valid_from"]["control"] == "date" and fact["valid_to"]["control"] == "date"
