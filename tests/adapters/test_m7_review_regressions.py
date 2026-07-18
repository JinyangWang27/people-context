"""Regression tests for the execution-verified M7 review findings."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter

from people_context.adapters.filesystem import FileSystemVaultWriter, MARKER_FILE
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteContextReader,
    SqliteGraphReader,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    SqliteVaultReader,
    open_db,
)
from people_context.app import (
    GetRelationshipGraph,
    NormalizeRelationships,
    SetRelationship,
    SetRelationshipInput,
)
from people_context.domain.person import Person
from people_context.domain.vault import VaultPerson, VaultSnapshot
from tests.app.fakes import FakeClock

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_CLOCK = FakeClock(_NOW)


def _people(conn, *names: str) -> list[Person]:
    repository = SqlitePeopleRepository(conn)
    people = [Person(canonical_name=name) for name in names]
    for person in people:
        repository.save_person(person)
    return people


def test_normalize_preserves_disjoint_history_and_keeps_active_overlap() -> None:
    conn = open_db(":memory:")
    a, b = _people(conn, "A", "B")
    disjoint = "01J00000000000000000000011"
    overlapping_expired = "01J00000000000000000000012"
    current = "01J00000000000000000000013"
    with conn:
        conn.executemany(
            """
            INSERT INTO relationships (
                id, subject_id, object_id, type, label, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES (?, ?, ?, 'reports_to', NULL, ?, ?, 1.0, 'legacy', NULL, NULL, ?)
            """,
            [
                (disjoint, a.id, b.id, "2010-01-01", "2011-12-31", "2010-01-01T00:00:00+00:00"),
                (
                    overlapping_expired,
                    a.id,
                    b.id,
                    "2019-01-01",
                    "2025-12-31",
                    "2019-01-01T00:00:00+00:00",
                ),
                (current, a.id, b.id, "2024-01-01", None, "2024-01-01T00:00:00+00:00"),
            ],
        )
    audit = SqliteAuditLog(conn)
    result = NormalizeRelationships(
        SqliteRelationshipStore(conn),
        SqliteRelationshipVocabularyStore(conn),
        audit,
        _CLOCK,
    ).execute(apply=True)

    assert [(change.action, change.relationship_id, change.merged_into) for change in result.changes] == [
        ("merge", overlapping_expired, current)
    ]
    rows = conn.execute(
        "SELECT id, valid_from, valid_to FROM relationships ORDER BY valid_from"
    ).fetchall()
    assert [(row["id"], row["valid_from"], row["valid_to"]) for row in rows] == [
        (disjoint, "2010-01-01", "2011-12-31"),
        (current, "2024-01-01", None),
    ]
    assert [entry.entity_id for entry in audit.list_entries()] == [overlapping_expired]
    assert [
        (entry.op_kind, entry.entity_id)
        for entry in SqliteChangelog(conn).list_entries(limit=10)
    ] == [("delete", overlapping_expired)]


def test_reassertion_keeps_omitted_fields_updates_provenance_and_changed_fields() -> None:
    conn = open_db(":memory:")
    a, b = _people(conn, "A", "B")
    audit = SqliteAuditLog(conn)
    use_case = SetRelationship(
        SqlitePeopleRepository(conn),
        SqliteRelationshipStore(conn),
        audit,
        _CLOCK,
        SqliteRelationshipVocabularyStore(conn),
    )
    first = use_case.execute(
        SetRelationshipInput(
            subject_id=a.id,
            object_id=b.id,
            type="friend_of",
            label="college friend",
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
            confidence=0.7,
            source="user",
            session="initial",
            stated_by="Jinyang",
        )
    )
    repeated = use_case.execute(
        SetRelationshipInput(
            subject_id=b.id,
            object_id=a.id,
            type="friend",
            source="agent:review",
            session="follow-up",
            stated_by="assistant",
        )
    )

    assert repeated.id == first.id
    assert repeated.label == "college friend"
    assert repeated.period.valid_from == date(2020, 1, 1)
    assert repeated.period.valid_to == date(2030, 12, 31)
    assert repeated.confidence == 0.7
    assert repeated.provenance.model_dump() == {
        "source": "agent:review",
        "session": "follow-up",
        "stated_by": "assistant",
    }
    latest = SqliteChangelog(conn).list_entries(limit=1)[0]
    assert latest.changed_fields == ["provenance"]
    assert latest.actor == {
        "source": "agent:review",
        "session": "follow-up",
        "stated_by": "assistant",
    }
    assert latest.payload["label"] == "college friend"
    assert latest.payload["period"] == {"valid_from": "2020-01-01", "valid_to": "2030-12-31"}


def test_marked_vault_regeneration_preserves_obsidian_and_user_paths(tmp_path: Path) -> None:
    output = tmp_path / "vault"
    writer = FileSystemVaultWriter()
    initial = VaultSnapshot(people=[VaultPerson(id="01AAAA00000000000000000000", name="Alice")])
    writer.write_vault(output, initial)
    generated_before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    obsidian = output / ".obsidian" / "workspace.json"
    obsidian.parent.mkdir()
    obsidian.write_text('{"layout":"user-owned"}\n', encoding="utf-8")
    user_note = output / "Notes" / "my-note.md"
    user_note.parent.mkdir()
    user_note.write_text("# Keep me\n", encoding="utf-8")
    scratch = output / "scratch.md"
    scratch.write_text("user root note\n", encoding="utf-8")
    stale_generated = output / "People" / "stale.md"
    stale_generated.write_text("stale\n", encoding="utf-8")

    writer.write_vault(output, initial)

    for relative, content in generated_before.items():
        assert (output / relative).read_bytes() == content
    assert (output / MARKER_FILE).is_file()
    assert obsidian.read_text(encoding="utf-8") == '{"layout":"user-owned"}\n'
    assert user_note.read_text(encoding="utf-8") == "# Keep me\n"
    assert scratch.read_text(encoding="utf-8") == "user root note\n"
    assert not stale_generated.exists()


def test_dense_graph_uses_fast_bfs_and_type_filters_resolve_vocabulary() -> None:
    conn = open_db(":memory:")
    repository = SqlitePeopleRepository(conn)
    clique = _people(conn, *(f"Person {index:02d}" for index in range(30)))
    outsider = _people(conn, "Outside")[0]
    rows: list[tuple[object, ...]] = []
    edge_number = 0
    for left_index, left in enumerate(clique):
        for right in clique[left_index + 1 :]:
            edge_number += 1
            subject_id, object_id = sorted((left.id, right.id))
            rows.append(
                (
                    f"{edge_number:026d}",
                    subject_id,
                    object_id,
                    "colleague_of",
                    "2025-01-01T00:00:00+00:00",
                )
            )
    with conn:
        conn.executemany(
            """
            INSERT INTO relationships (
                id, subject_id, object_id, type, confidence, provenance_source, created_at
            ) VALUES (?, ?, ?, ?, 1.0, 'test', ?)
            """,
            rows,
        )
    reader = SqliteGraphReader(conn, _CLOCK)
    started = perf_counter()
    subgraph = reader.neighbors(clique[0].id, 4)
    neighbors_elapsed = perf_counter() - started
    started = perf_counter()
    path = reader.path_between(clique[0].id, outsider.id, 4)
    path_elapsed = perf_counter() - started

    assert len(subgraph.nodes) == 30
    assert len(subgraph.edges) == 435
    assert path is None
    assert neighbors_elapsed < 1.0
    assert path_elapsed < 1.0

    a, b = _people(conn, "A", "B")
    SetRelationship(
        repository,
        SqliteRelationshipStore(conn),
        SqliteAuditLog(conn),
        _CLOCK,
        SqliteRelationshipVocabularyStore(conn),
    ).execute(SetRelationshipInput(subject_id=a.id, object_id=b.id, type="friend_of"))
    graph = GetRelationshipGraph(
        repository,
        reader,
        SqliteRelationshipVocabularyStore(conn),
    ).execute(a.id, depth=1, types=["friend"])
    assert [edge.type for edge in graph.edges] == ["friend_of"]


def test_valid_from_bounds_and_injected_clocks_control_all_active_reads() -> None:
    conn = open_db(":memory:")
    repository = SqlitePeopleRepository(conn)
    a, b = _people(conn, "A", "B")
    present_clock = FakeClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    future_clock = FakeClock(datetime(2031, 1, 1, 12, 0, tzinfo=UTC))
    store = SqliteRelationshipStore(conn)
    SetRelationship(
        repository,
        store,
        SqliteAuditLog(conn),
        present_clock,
        SqliteRelationshipVocabularyStore(conn),
    ).execute(
        SetRelationshipInput(
            subject_id=a.id,
            object_id=b.id,
            type="friend_of",
            valid_from=date(2030, 1, 1),
        )
    )
    with conn:
        conn.execute("INSERT INTO organizations (id, name, kind) VALUES ('future-org', 'Future Org', 'company')")
        conn.execute(
            """
            INSERT INTO affiliations (
                id, person_id, org_id, role, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES (
                'future-affiliation', ?, 'future-org', 'Advisor', '2030-01-01', NULL,
                1.0, 'test', NULL, NULL, '2026-01-01T00:00:00+00:00'
            )
            """,
            (a.id,),
        )

    context = SqliteContextReader(conn)
    assert store.find_active_relationship(a.id, b.id, "friend_of", date(2026, 1, 1)) is None
    assert context.list_active_relationships(a.id, date(2026, 1, 1)) == []
    assert context.list_active_affiliations(a.id, date(2026, 1, 1)) == []
    present_nodes = SqliteGraphReader(conn, present_clock).neighbors(a.id, 1).nodes
    assert [node.person_id for node in present_nodes] == [a.id]
    present_vault = next(
        person for person in SqliteVaultReader(conn, present_clock).read_vault().people if person.id == a.id
    )
    assert present_vault.relationships == []
    assert present_vault.affiliations == []

    assert store.find_active_relationship(a.id, b.id, "friend_of", date(2031, 1, 1)) is not None
    assert len(context.list_active_relationships(a.id, date(2031, 1, 1))) == 1
    assert len(context.list_active_affiliations(a.id, date(2031, 1, 1))) == 1
    assert {node.person_id for node in SqliteGraphReader(conn, future_clock).neighbors(a.id, 1).nodes} == {
        a.id,
        b.id,
    }
    future_vault = next(
        person for person in SqliteVaultReader(conn, future_clock).read_vault().people if person.id == a.id
    )
    assert [relationship.display_type for relationship in future_vault.relationships] == ["friend_of"]
    assert [affiliation.org_name for affiliation in future_vault.affiliations] == ["Future Org"]
