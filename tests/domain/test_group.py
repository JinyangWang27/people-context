"""Domain rules for identified groups and memberships (M28.1)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from people_context.domain.group import MAX_GROUP_NAME_CHARS, Group, GroupKind, GroupMembership, TemporalBasis
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod

_PROVENANCE = Provenance(source="test")


def test_a_group_defaults_to_personal_and_strips_its_name() -> None:
    group = Group(name="  Class 1  ", kind=GroupKind.CLASS, provenance=_PROVENANCE)

    assert (group.name, group.sensitivity) == ("Class 1", Sensitivity.PERSONAL)


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_GROUP_NAME_CHARS + 1)])
def test_a_blank_or_overlong_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        Group(name=name, kind=GroupKind.CLASS, provenance=_PROVENANCE)


def test_an_unlisted_kind_or_role_is_refused() -> None:
    with pytest.raises(ValidationError):
        Group(name="Class 1", kind="school", provenance=_PROVENANCE)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GroupMembership(person_id="P", group_id="G", role="captain", provenance=_PROVENANCE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("basis", "period", "valid"),
    [
        (TemporalBasis.UNKNOWN, ValidityPeriod(), True),
        (TemporalBasis.UNKNOWN, ValidityPeriod(valid_to=date(2016, 7, 1)), False),
        (TemporalBasis.PERIOD, ValidityPeriod(valid_to=date(2016, 7, 1)), True),
        (TemporalBasis.PERIOD, ValidityPeriod(), False),
        (TemporalBasis.ONGOING, ValidityPeriod(), True),
        (TemporalBasis.ONGOING, ValidityPeriod(valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1)), False),
    ],
)
def test_the_basis_states_what_the_dates_assert(basis: TemporalBasis, period: ValidityPeriod, valid: bool) -> None:
    def build() -> GroupMembership:
        return GroupMembership(person_id="P", group_id="G", period=period, temporal_basis=basis, provenance=_PROVENANCE)

    if valid:
        assert build().temporal_basis is basis
    else:
        with pytest.raises(ValidationError):
            build()
