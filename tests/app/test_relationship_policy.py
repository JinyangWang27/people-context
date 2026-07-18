"""Pure relationship vocabulary policy tests."""

from __future__ import annotations

from people_context.app.relationship_policy import normalize_relationship, relationship_display_type
from people_context.domain.relationship_vocabulary import RelationshipType


class Vocabulary:
    def __init__(self) -> None:
        self.rows = {
            "reports_to": RelationshipType(
                type="reports_to", inverse="manages", category="professional", canonical=True
            ),
            "manages": RelationshipType(
                type="manages", inverse="reports_to", category="professional", canonical=False
            ),
            "friend_of": RelationshipType(type="friend_of", symmetric=True, category="social"),
            "manager_of": RelationshipType(
                type="manages", inverse="reports_to", category="professional", canonical=False
            ),
        }

    def resolve(self, value: str) -> RelationshipType | None:
        return self.rows.get(value.replace(" ", "_"))

    def list_types(self) -> list[RelationshipType]:
        return []

    def list_uncategorized_types(self) -> list[str]:
        return []


def test_normalizes_inverse_symmetric_and_unknown_types() -> None:
    vocabulary = Vocabulary()
    inverse = normalize_relationship("B", "A", "manager of", vocabulary)
    symmetric = normalize_relationship("B", "A", "friend of", vocabulary)
    unknown = normalize_relationship("A", "B", "Childhood Rival Of", vocabulary)

    assert (inverse.subject_id, inverse.object_id, inverse.type) == ("A", "B", "reports_to")
    assert (symmetric.subject_id, symmetric.object_id, symmetric.type) == ("A", "B", "friend_of")
    assert (unknown.type, unknown.category) == ("childhood_rival_of", "uncategorized")
    assert relationship_display_type(
        "reports_to", queried_person_id="B", subject_id="A", vocabulary=vocabulary
    ) == "manages"
