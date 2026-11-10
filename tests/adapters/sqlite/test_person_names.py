"""Explicit names survive ordinary writes and portable bootstrap restore."""

import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import SqlitePeopleRepository, open_db
from people_context.app.exports import PersonIndexError
from people_context.app.people import EditPersonInput, RememberPersonInput


def test_names_edit_search_sort_and_restore(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "source.db")
    try:
        use_cases = runtime.use_cases
        person = use_cases.remember_person.execute(
            RememberPersonInput(name="王小明", preferred_name="Ming", given_name="小明", family_name="王")
        ).person
        use_cases.remember_person.execute(RememberPersonInput(name="Aaron Jones", preferred_name="Zed"))
        stored = runtime.repo.get(person.id)
        assert stored is not None
        assert (stored.preferred_name, stored.given_name, stored.family_name) == ("Ming", "小明", "王")
        page = use_cases.list_person_index.page(limit=1)
        assert page.people[0].id == person.id
        assert page.people[0].preferred_name == "Ming"
        assert page.next_cursor
        cursor = page.next_cursor
        assert (
            use_cases.list_person_index.page(limit=1, cursor=page.next_cursor).people[0].canonical_name == "Aaron Jones"
        )
        for query in ("ming", "小明", "王"):
            assert use_cases.list_person_index.page(query=query).people[0].id == person.id
        assert runtime.repo.search_names("Ming")[0].person.id == person.id
        # Components are searchable, but do not become new automatic identity matching keys.
        assert runtime.repo.find_by_normalized_name("ming") == []
        use_cases.edit_person.execute(EditPersonInput(person_id=person.id, summary="Friend"))
        assert runtime.repo.get(person.id).preferred_name == "Ming"
        use_cases.edit_person.execute(EditPersonInput(person_id=person.id, preferred_name=""))
        assert runtime.repo.get(person.id).display_name == "小明"
        with pytest.raises(PersonIndexError):
            use_cases.list_person_index.page(limit=1, cursor=cursor)
        bundle = use_cases.export_sync_bundle.execute()
        assert bundle.version == 9
        edits = [entry for entry in bundle.changelog if "preferred_name" in entry.changed_fields]
        assert any(entry.payload["preferred_name"] is None for entry in edits)
    finally:
        runtime.close()

    target = build_runtime(tmp_path / "target.db")
    try:
        target.use_cases.restore_sync_bundle.execute(bundle)
        restored = target.repo.get(person.id)
        assert restored is not None
        assert (restored.preferred_name, restored.given_name, restored.family_name) == (None, "小明", "王")
    finally:
        target.close()


def test_version_10_migration_preserves_existing_identity(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    schema = resources.files("people_context.adapters.sqlite.migrations").joinpath("010_schema.sql").read_text()
    conn.executescript(schema)
    conn.execute("PRAGMA user_version = 10")
    conn.execute(
        "INSERT INTO persons (id, canonical_name, canonical_name_normalized, created_at, updated_at) "
        "VALUES ('old', '王小明', '王小明', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    upgraded = open_db(path)
    try:
        person = SqlitePeopleRepository(upgraded).get("old")
        assert person is not None
        assert person.canonical_name == person.display_name == "王小明"
        assert (person.preferred_name, person.given_name, person.family_name) == (None, None, None)
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 12
        assert upgraded.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
        assert upgraded.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
    finally:
        upgraded.close()


def test_merge_keeps_primary_names_and_fills_missing_components(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "merge.db")
    try:
        primary = runtime.use_cases.remember_person.execute(
            RememberPersonInput(name="Alice Zhang", preferred_name="Ali")
        ).person
        duplicate = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name="A. Zhang", preferred_name="Ally", given_name="Alice", family_name="Zhang", family_name_first=True
            )
        ).person
        runtime.use_cases.merge_people.execute(primary.id, duplicate.id)
        person = runtime.repo.get(primary.id)
        assert person is not None
        assert (person.preferred_name, person.given_name, person.family_name) == ("Ali", "Alice", "Zhang")
        assert "Ally" in person.all_names()
        assert person.family_name_first is True
        assert runtime.repo.search_names("Ali")[0].person.id == primary.id
    finally:
        runtime.close()


def test_duplicate_preferred_names_page_once_and_search_normalized_components(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "paging.db")
    try:
        ids = {
            runtime.use_cases.remember_person.execute(
                RememberPersonInput(
                    name=name,
                    preferred_name="Álex",
                    given_name="Émile",
                    family_name="Björk",
                )
            ).person.id
            for name in ("Alpha Jones", "Zoe Smith", "Morgan Lee")
        }
        seen = []
        cursor = None
        while True:
            page = runtime.use_cases.list_person_index.page(limit=1, cursor=cursor)
            seen.extend(person.id for person in page.people)
            cursor = page.next_cursor
            if not cursor:
                break
        assert len(seen) == 3
        assert set(seen) == ids
        for query in ("alex", "emile", "bjork"):
            assert {p.id for p in runtime.use_cases.list_person_index.page(query=query).people} == ids
    finally:
        runtime.close()


def test_chinese_name_is_found_by_explicit_pinyin_alias_without_changing_display(tmp_path: Path) -> None:
    from people_context.app.capture import QuickCaptureInput
    from people_context.app.people import AliasInput
    from people_context.domain.person import AliasKind

    runtime = build_runtime(tmp_path / "chinese-names.db")
    try:
        person = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name="王小明",
                preferred_name="小明",
                given_name="小明",
                family_name="王",
                aliases=[
                    AliasInput(value="Xiaoming", kind=AliasKind.TRANSLITERATION),
                    AliasInput(value="Wang Xiaoming", kind=AliasKind.TRANSLITERATION),
                ],
            )
        ).person
        for reference in ("Xiaoming", "Wang Xiaoming", "王小明"):
            result = runtime.use_cases.resolve_person.execute(reference)
            assert result.ambiguous is False
            assert result.candidates[0].person_id == person.id
            assert result.candidates[0].match_reason == "exact"
        capture = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Xiaoming", note="moved to Shanghai")
        )
        assert capture.status == "recorded" and capture.person_id == person.id
        assert capture.created is False
        assert runtime.repo.get(person.id).display_name == "小明"
        assert runtime.use_cases.list_person_index.page(query="xiaoming").people[0].display_name == "小明"
        other = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name="李晓明",
                preferred_name="晓明",
                aliases=[AliasInput(value="Xiaoming", kind=AliasKind.TRANSLITERATION)],
            )
        ).person
        result = runtime.use_cases.resolve_person.execute("Xiaoming")
        assert result.ambiguous is True
        assert {candidate.person_id for candidate in result.candidates} == {person.id, other.id}
        capture = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Xiaoming", note="moved to Beijing"))
        assert capture.status == "ambiguous" and capture.recorded == []
    finally:
        runtime.close()


def test_family_name_order_survives_edit_omission_and_restore(tmp_path: Path) -> None:
    from people_context.adapters.importers.vcard import VCardImportExtractor

    runtime = build_runtime(tmp_path / "order.db")
    try:
        person = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name="Wang Xiaoming",
                preferred_name="小明",
                given_name="小明",
                family_name="王",
                family_name_first=True,
            )
        ).person
        assert runtime.repo.get(person.id).full_name == "王小明"
        runtime.use_cases.edit_person.execute(EditPersonInput(person_id=person.id, summary="Friend"))
        assert runtime.repo.get(person.id).family_name_first is True
        entry = runtime.use_cases.list_person_index.page().people[0]
        assert entry.full_name == "王小明" and entry.family_name_first is True
        assert runtime.use_cases.list_person_index.page(query="王小明").people[0].id == person.id
        assert runtime.repo.search_names("王小明")[0].person.id == person.id
        vcard = runtime.use_cases.export_vcard.execute().document
        assert "FN:王小明\r\n" in vcard
        reimported = VCardImportExtractor().extract("vcard", content=vcard, path=None, self_addresses=set())
        assert reimported.candidates[0]["family_name_first"] is True
        assert reimported.candidates[0]["given_name"] == "小明"
        assert reimported.candidates[0]["family_name"] == "王"
        bundle = runtime.use_cases.export_sync_bundle.execute()
        assert bundle.snapshot.people[0].family_name_first is True
        runtime.use_cases.edit_person.execute(EditPersonInput(person_id=person.id, family_name_first=False))
        assert runtime.repo.get(person.id).full_name == "小明王"
    finally:
        runtime.close()
    target = build_runtime(tmp_path / "restored-order.db")
    try:
        target.use_cases.restore_sync_bundle.execute(bundle)
        restored = target.repo.get(person.id)
        assert restored.family_name_first is True and restored.full_name == "王小明"
    finally:
        target.close()
