"""Tests for Person, Alias, and related enums / confidence bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Provenance, Sensitivity
from people_context.domain.trait import Trait, TraitCategory


def test_person_defaults() -> None:
    person = Person(canonical_name="Ada Lovelace")
    assert person.is_self is False
    assert person.summary is None
    assert person.aliases == []
    assert person.deleted_at is None
    assert len(person.id) == 26
    assert person.created_at.tzinfo is not None


def test_all_names_dedup_and_order() -> None:
    person = Person(
        canonical_name="Jinyang Wang",
        aliases=[
            Alias(value="JW", kind=AliasKind.NICKNAME),
            Alias(value="王金阳", kind=AliasKind.NATIVE_SCRIPT),
            Alias(value="Jinyang Wang", kind=AliasKind.OTHER),  # duplicate of canonical
            Alias(value="JW", kind=AliasKind.HANDLE),  # duplicate alias
        ],
    )
    assert person.all_names() == ["Jinyang Wang", "JW", "王金阳"]


def test_alias_kinds() -> None:
    assert AliasKind.NICKNAME == "nickname"
    assert AliasKind.NATIVE_SCRIPT == "native_script"
    assert AliasKind.TRANSLITERATION == "transliteration"
    assert AliasKind.HANDLE == "handle"
    assert AliasKind.FORMER_NAME == "former_name"
    assert AliasKind.OTHER == "other"


def test_confidence_within_bounds_ok() -> None:
    trait = Trait(
        person_id="p1",
        category=TraitCategory.COMMUNICATION_STYLE,
        value="direct",
        confidence=0.0,
        provenance=Provenance(source="user"),
    )
    assert trait.confidence == 0.0


def test_confidence_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        Trait(
            person_id="p1",
            category=TraitCategory.TEMPERAMENT,
            value="calm",
            confidence=1.5,
            provenance=Provenance(source="user"),
        )


def test_confidence_below_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        Trait(
            person_id="p1",
            category=TraitCategory.VALUES,
            value="honesty",
            confidence=-0.1,
            provenance=Provenance(source="user"),
        )


def test_sensitivity_enum_values() -> None:
    assert [s.value for s in Sensitivity] == ["public", "personal", "sensitive", "restricted"]
