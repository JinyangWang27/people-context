"""Integration tests for SQLite contextual-record hydration."""

from __future__ import annotations

from datetime import UTC, date, datetime

from people_context.adapters.sqlite import SqliteContextReader, SqlitePeopleRepository, open_db
from people_context.domain.person import Person
from people_context.domain.reminder import ReminderKind
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory
from people_context.ports.context import PersonContextReader

_TS = datetime(2025, 1, 1, 12, tzinfo=UTC).isoformat()


def test_hydrates_every_context_type_with_joins_directions_and_filters() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    target = Person(canonical_name="Alice")
    manager = Person(canonical_name="Morgan")
    report = Person(canonical_name="Robin")
    outsider = Person(canonical_name="Taylor")
    for person in (target, manager, report, outsider):
        people.save_person(person)

    with conn:
        conn.execute("INSERT INTO organizations (id, name, kind) VALUES ('org-1', 'Acme', 'company')")
        conn.execute("INSERT INTO organizations (id, name, kind) VALUES ('org-2', 'OldCo', 'company')")
        conn.executemany(
            """
            INSERT INTO affiliations (
                id, person_id, org_id, role, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("aff-active", target.id, "org-1", "Engineer", None, None, 0.8, "test", "s1", "self", _TS),
                ("aff-expired", target.id, "org-2", "Intern", None, "2024-12-31", 1.0, "test", None, None, _TS),
            ],
        )
        conn.executemany(
            """
            INSERT INTO relationships (
                id, subject_id, object_id, type, label, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "rel-incoming",
                    manager.id,
                    target.id,
                    "manager_of",
                    "Direct manager",
                    None,
                    None,
                    0.9,
                    "test",
                    None,
                    None,
                    _TS,
                ),
                (
                    "rel-outgoing",
                    target.id,
                    report.id,
                    "mentor_of",
                    None,
                    "2025-01-01",
                    None,
                    1.0,
                    "test",
                    None,
                    None,
                    _TS,
                ),
                (
                    "rel-expired",
                    target.id,
                    outsider.id,
                    "former_peer",
                    None,
                    None,
                    "2024-12-31",
                    1.0,
                    "test",
                    None,
                    None,
                    _TS,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO facts (
                id, person_id, predicate, value, valid_from, valid_to, recorded_at,
                confidence, sensitivity, provenance_source, provenance_session, provenance_stated_by
            ) VALUES ('fact-1', ?, 'location', 'Dubai', NULL, NULL, ?, 0.7, 'public', 'test', 's1', 'self')
            """,
            (target.id, _TS),
        )
        conn.execute(
            """
            INSERT INTO observations (
                id, person_id, text, observed_at, sensitivity,
                provenance_source, provenance_session, provenance_stated_by
            ) VALUES ('observation-1', ?, 'Calm under pressure', ?, 'personal', 'test', NULL, NULL)
            """,
            (target.id, _TS),
        )
        conn.execute(
            """
            INSERT INTO traits (
                id, person_id, category, value, evidence_note, confidence, sensitivity,
                provenance_source, provenance_session, provenance_stated_by, updated_at
            ) VALUES (
                'trait-1', ?, 'communication_style', 'Concise', 'Observed in meetings', 0.6,
                'sensitive', 'test', NULL, NULL, ?
            )
            """,
            (target.id, _TS),
        )
        conn.executemany(
            """
            INSERT INTO interactions (
                id, summary, occurred_at, channel, sensitivity,
                provenance_source, provenance_session, provenance_stated_by
            ) VALUES (?, ?, ?, ?, ?, 'test', NULL, NULL)
            """,
            [
                ("interaction-target", "Launch review", _TS, "call", "personal"),
                ("interaction-other", "Unrelated", _TS, "email", "public"),
            ],
        )
        conn.executemany(
            "INSERT INTO interaction_participants (interaction_id, person_id) VALUES (?, ?)",
            [
                ("interaction-target", target.id),
                ("interaction-target", manager.id),
                ("interaction-other", outsider.id),
            ],
        )
        conn.executemany(
            """
            INSERT INTO reminders (
                id, person_id, text, kind, due_at, recurrence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("reminder-active", target.id, "Write first", "communication_note", None, None, "active", _TS),
                ("reminder-done", target.id, "Old", "follow_up", _TS, None, "completed", _TS),
            ],
        )

    reader = SqliteContextReader(conn)
    relationships = reader.list_active_relationships(target.id, date(2025, 1, 1))
    affiliations = reader.list_active_affiliations(target.id, date(2025, 1, 1))
    facts = reader.list_facts(target.id)
    observations = reader.list_observations(target.id)
    traits = reader.list_traits(target.id)
    interactions = reader.list_interactions(target.id)
    reminders = reader.list_active_reminders(target.id)

    assert {record.relationship.id for record in relationships} == {"rel-incoming", "rel-outgoing"}
    assert {(record.other_person_id, record.other_person_name) for record in relationships} == {
        (manager.id, "Morgan"),
        (report.id, "Robin"),
    }
    assert relationships[0].relationship.provenance.source == "test"
    assert [(record.affiliation.id, record.organization_name) for record in affiliations] == [("aff-active", "Acme")]
    assert facts[0].predicate == "location"
    assert facts[0].confidence == 0.7
    assert facts[0].sensitivity == Sensitivity.PUBLIC
    assert observations[0].text == "Calm under pressure"
    assert traits[0].category == TraitCategory.COMMUNICATION_STYLE
    assert traits[0].evidence_note == "Observed in meetings"
    assert [interaction.id for interaction in interactions] == ["interaction-target"]
    assert set(interactions[0].participant_ids) == {target.id, manager.id}
    assert [reminder.kind for reminder in reminders] == [ReminderKind.COMMUNICATION_NOTE]


def test_sqlite_context_reader_satisfies_port_structurally() -> None:
    reader: PersonContextReader = SqliteContextReader(open_db(":memory:"))
    assert isinstance(reader, PersonContextReader)
