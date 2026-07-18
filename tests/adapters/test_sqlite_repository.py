"""Tests for the SQLite persistence adapter: db/migrations, repository, audit log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.repository import PersonReader, PersonWriter

_EXPECTED_TABLES = {
    "persons",
    "aliases",
    "organizations",
    "affiliations",
    "relationships",
    "relationship_types",
    "relationship_type_synonyms",
    "facts",
    "observations",
    "traits",
    "interactions",
    "interaction_participants",
    "reminders",
    "user_preferences",
    "import_staging",
    "audit_log",
    "person_search",
}


def _person(**overrides: object) -> Person:
    base: dict[str, object] = {
        "canonical_name": "Wang Xiaoming",
        "aliases": [
            Alias(value="小明", kind=AliasKind.NATIVE_SCRIPT, lang="zh", script="Hans"),
            Alias(value="Ming", kind=AliasKind.NICKNAME),
        ],
        "created_at": datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "updated_at": datetime(2024, 6, 7, 8, 9, 10, tzinfo=timezone(timedelta(hours=8))),
    }
    base.update(overrides)
    return Person(**base)  # type: ignore[arg-type]


# -- migrations / db --------------------------------------------------------


def test_fresh_db_has_user_version_and_all_tables() -> None:
    conn = open_db(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= _EXPECTED_TABLES


def test_reopening_db_is_idempotent(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "people.db"
    open_db(db_file).close()
    conn = open_db(db_file)  # parent dirs already created; migrations already applied
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0


# -- round-trip -------------------------------------------------------------


def test_save_and_get_round_trip_preserves_all_fields() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person(is_self=True, summary="Colleague from Acme")
    repo.save_person(person)

    loaded = repo.get(person.id)
    assert loaded is not None
    assert loaded.canonical_name == person.canonical_name
    assert loaded.is_self is True
    assert loaded.summary == "Colleague from Acme"
    assert loaded.created_at == person.created_at
    assert loaded.updated_at == person.updated_at
    assert loaded.created_at.tzinfo is not None
    assert {(a.value, a.kind, a.lang, a.script) for a in loaded.aliases} == {
        ("小明", AliasKind.NATIVE_SCRIPT, "zh", "Hans"),
        ("Ming", AliasKind.NICKNAME, None, None),
    }


def test_resave_replaces_aliases() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person()
    repo.save_person(person)

    person.aliases = [Alias(value="小明", kind=AliasKind.NATIVE_SCRIPT)]
    repo.save_person(person)

    loaded = repo.get(person.id)
    assert loaded is not None
    assert [a.value for a in loaded.aliases] == ["小明"]
    assert repo.find_by_normalized_name(normalize_name("Ming")) == []


# -- find_by_normalized_name ------------------------------------------------


def test_find_by_normalized_name_canonical_and_alias() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person()
    repo.save_person(person)

    by_canonical = repo.find_by_normalized_name(normalize_name("wang  xiaoming"))
    by_alias = repo.find_by_normalized_name(normalize_name("小明"))
    assert [p.id for p in by_canonical] == [person.id]
    assert [p.id for p in by_alias] == [person.id]


def test_find_by_normalized_name_excludes_soft_deleted() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person(deleted_at=datetime(2024, 9, 1, tzinfo=UTC))
    repo.save_person(person)
    assert repo.find_by_normalized_name(normalize_name("Wang Xiaoming")) == []


# -- search_names -----------------------------------------------------------


def test_search_prefix_match_on_canonical() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person()
    repo.save_person(person)

    hits = repo.search_names("wan")
    assert [h.person.id for h in hits] == [person.id]
    assert hits[0].match_kind == "canonical"
    assert hits[0].matched_value == "Wang Xiaoming"
    assert 0.0 < hits[0].score <= 1.0


def test_search_alias_and_cjk_match() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person()
    repo.save_person(person)

    nickname_hits = repo.search_names("Ming")
    assert [h.person.id for h in nickname_hits] == [person.id]
    assert nickname_hits[0].match_kind == "alias"

    cjk_hits = repo.search_names("小明")
    assert [h.person.id for h in cjk_hits] == [person.id]
    assert cjk_hits[0].matched_value == "小明"
    assert cjk_hits[0].match_kind == "alias"


def test_search_excludes_deleted_and_scores_bounded() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    live = _person(canonical_name="Zhang Wei")
    dead = _person(canonical_name="Zhang Min", aliases=[], deleted_at=datetime(2024, 5, 5, tzinfo=UTC))
    repo.save_person(live)
    repo.save_person(dead)

    hits = repo.search_names("zhang")
    ids = {h.person.id for h in hits}
    assert live.id in ids
    assert dead.id not in ids
    assert all(0.0 < h.score <= 1.0 for h in hits)


def test_search_dedupes_keeping_single_hit_per_person() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person(canonical_name="Ming Zhao", aliases=[Alias(value="Ming", kind=AliasKind.NICKNAME)])
    repo.save_person(person)

    hits = repo.search_names("ming")
    assert len(hits) == 1
    assert hits[0].person.id == person.id


def test_search_reflects_rename_in_fts() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person(canonical_name="Alice", aliases=[])
    repo.save_person(person)
    assert [h.person.id for h in repo.search_names("alice")] == [person.id]

    person.canonical_name = "Bob"
    repo.save_person(person)
    assert repo.search_names("alice") == []
    assert [h.person.id for h in repo.search_names("bob")] == [person.id]


def test_search_falls_back_to_substring() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    person = _person(canonical_name="Alexandra", aliases=[])
    repo.save_person(person)

    hits = repo.search_names("xand")
    assert [h.person.id for h in hits] == [person.id]
    assert 0.0 < hits[0].score <= 1.0


# -- get_self ---------------------------------------------------------------


def test_get_self_returns_is_self_row() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    me = _person(canonical_name="Me", aliases=[], is_self=True)
    other = _person(canonical_name="Someone", aliases=[])
    repo.save_person(other)
    repo.save_person(me)

    found = repo.get_self()
    assert found is not None
    assert found.id == me.id


def test_list_people_orders_and_filters_deleted() -> None:
    repo = SqlitePeopleRepository(open_db(":memory:"))
    repo.save_person(_person(canonical_name="Charlie", aliases=[]))
    repo.save_person(_person(canonical_name="Alice", aliases=[]))
    repo.save_person(_person(canonical_name="Gone", aliases=[], deleted_at=datetime(2024, 1, 1, tzinfo=UTC)))

    names = [p.canonical_name for p in repo.list_people()]
    assert names == ["Alice", "Charlie"]
    assert len(repo.list_people(include_deleted=True)) == 3
    assert len(repo.list_people(limit=1)) == 1


# -- audit log --------------------------------------------------------------


def test_audit_log_append_and_list_newest_first() -> None:
    conn = open_db(":memory:")
    audit = SqliteAuditLog(conn)
    first = AuditEntry(
        ts=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        op="create",
        entity_type="person",
        entity_id="p1",
        payload={"canonical_name": "Wang Xiaoming", "aliases": ["Ming"]},
        source="user",
    )
    second = AuditEntry(
        ts=datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC),
        op="update",
        entity_type="person",
        entity_id="p1",
        payload={},
        source="agent:claude-code",
    )
    audit.append(first)
    audit.append(second)

    entries = audit.list_entries()
    assert [e.id for e in entries] == [second.id, first.id]
    assert entries[1].payload == {"canonical_name": "Wang Xiaoming", "aliases": ["Ming"]}
    assert entries[0].ts == second.ts


# -- structural Protocol conformance ---------------------------------------


def test_adapters_satisfy_ports_structurally() -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)

    reader: PersonReader = repo
    writer: PersonWriter = repo
    log: AuditLog = audit

    assert isinstance(reader, PersonReader)
    assert isinstance(writer, PersonWriter)
    assert isinstance(log, AuditLog)
