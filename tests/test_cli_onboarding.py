"""Safe interactive onboarding and isolated packaged demo CLI tests."""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import SqliteAuditLog, SqliteGraphReader, SqlitePeopleRepository, open_db
from people_context.app import AliasInput, ImportReviewRow, RememberPerson, RememberPersonInput
from people_context.domain.person import AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.ports.clock import SystemClock


def _seed(db_path: Path, name: str, **kwargs: object) -> Person:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        return RememberPerson(repo, repo, SqliteAuditLog(conn), SystemClock()).execute(
            RememberPersonInput(name=name, **kwargs)  # type: ignore[arg-type]
        ).person
    finally:
        conn.close()


def _input_sequence(monkeypatch: pytest.MonkeyPatch, values: list[str]) -> None:
    responses: Iterator[str] = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))


def test_init_fresh_creates_normalized_self_handles_and_philosophy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "fresh.db"
    _input_sequence(
        monkeypatch,
        ["  Maya   Chen ", " MAYA@EXAMPLE.COM, maya@example.com ", "", "Prefer concise written updates"],
    )

    assert cli.main(["--db", str(db_path), "init"]) == 0
    assert "Onboarding complete." in capsys.readouterr().out

    conn = open_db(db_path)
    try:
        person = SqlitePeopleRepository(conn).get_self()
        philosophy = conn.execute(
            "SELECT value_json FROM user_preferences WHERE key = ?",
            (PREF_COMMUNICATION_PHILOSOPHY,),
        ).fetchone()
    finally:
        conn.close()
    assert person is not None
    assert person.canonical_name == "Maya Chen"
    assert [(alias.value, alias.kind) for alias in person.aliases] == [
        ("maya@example.com", AliasKind.HANDLE)
    ]
    assert philosophy is not None and philosophy[0] == '"Prefer concise written updates"'


def test_init_additive_keeps_existing_self_identity_and_adds_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "additive.db"
    existing = _seed(db_path, "Existing Self", is_self=True)
    _seed(db_path, "Known Contact")
    _input_sequence(monkeypatch, ["yes", "SELF@EXAMPLE.COM", "", ""])

    assert cli.main(["--db", str(db_path), "init"]) == 0

    conn = open_db(db_path)
    try:
        people = SqlitePeopleRepository(conn).list_people()
        self_person = SqlitePeopleRepository(conn).get_self()
    finally:
        conn.close()
    assert len(people) == 2
    assert self_person is not None and self_person.id == existing.id
    assert [(alias.value, alias.kind) for alias in self_person.aliases] == [
        ("self@example.com", AliasKind.HANDLE)
    ]


def test_init_additive_refuses_handle_owned_by_another_person_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "handle-collision.db"
    existing = _seed(db_path, "Existing Self", is_self=True)
    _seed(
        db_path,
        "Known Contact",
        aliases=[AliasInput(value="known@example.com", kind=AliasKind.HANDLE)],
    )
    _input_sequence(monkeypatch, ["yes", "KNOWN@EXAMPLE.COM"])

    assert cli.main(["--db", str(db_path), "init"]) == 1
    conn = open_db(db_path)
    try:
        self_person = SqlitePeopleRepository(conn).get(existing.id)
    finally:
        conn.close()
    assert self_person is not None and self_person.aliases == []


@pytest.mark.parametrize("ambiguous", [False, True])
def test_init_refuses_nonempty_state_without_one_unambiguous_self_before_prompt_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ambiguous: bool,
) -> None:
    db_path = tmp_path / "refused.db"
    if ambiguous:
        self_person = _seed(db_path, "Existing Self", is_self=True)
        _seed(db_path, "Other", aliases=[AliasInput(value="Existing Self")])
    else:
        self_person = _seed(db_path, "Existing Person")
    def unexpected_prompt(_: str) -> str:
        raise AssertionError("refused init must not prompt")

    monkeypatch.setattr("builtins.input", unexpected_prompt)
    assert cli.main(["--db", str(db_path), "init"]) == 1
    assert "Cannot continue" in capsys.readouterr().err
    conn = open_db(db_path)
    try:
        persisted = SqlitePeopleRepository(conn).get(self_person.id)
    finally:
        conn.close()
    assert persisted is not None and persisted.aliases == []


def test_init_abort_and_unreadable_vcard_are_non_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "abort.db"
    _seed(db_path, "Existing Self", is_self=True)
    _input_sequence(monkeypatch, ["no"])
    assert cli.main(["--db", str(db_path), "init"]) == 0

    fresh_path = tmp_path / "unreadable.db"
    _input_sequence(monkeypatch, ["Maya Chen", "maya@example.com", str(tmp_path / "missing.vcf")])
    assert cli.main(["--db", str(fresh_path), "init"]) == 1
    conn = open_db(fresh_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    finally:
        conn.close()


def test_init_vcard_excludes_self_card_and_dependents_and_commits_explicit_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "vcard.db"
    vcard_path = tmp_path / "contacts.vcf"
    vcard_path.write_text(
        "\n".join(
            [
                "BEGIN:VCARD",
                "VERSION:4.0",
                "FN:Maya Chen",
                "EMAIL:maya@example.com",
                "ORG:Private Self Org",
                "TITLE:Owner",
                "BDAY:1990-01-01",
                "END:VCARD",
                "BEGIN:VCARD",
                "VERSION:4.0",
                "FN:Alice Example",
                "EMAIL:alice@example.com",
                "ORG:Acme",
                "TITLE:Engineer",
                "BDAY:1992-02-02",
                "END:VCARD",
            ]
        ),
        encoding="utf-8",
    )
    prompt_count = 0

    def answer(prompt: str) -> str:
        nonlocal prompt_count
        prompt_count += 1
        if prompt.startswith("Canonical"):
            return "Maya Chen"
        if prompt.startswith("Email"):
            return "maya@example.com"
        if prompt.startswith("vCard"):
            return str(vcard_path)
        if prompt.startswith("Candidate IDs"):
            conn = open_db(db_path)
            try:
                rows = conn.execute(
                    "SELECT id, json_extract(candidate_json, '$.type') AS type "
                    "FROM import_staging ORDER BY created_at, id"
                ).fetchall()
            finally:
                conn.close()
            # Accept the person and affiliation, deliberately leave the birthday fact pending.
            return ",".join(row["id"] for row in rows if row["type"] != "fact")
        if prompt.startswith("Communication"):
            return ""
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["--db", str(db_path), "init"]) == 0
    output = capsys.readouterr().out
    assert "Private Self Org" not in output
    assert "Alice Example" in output
    assert prompt_count == 5

    conn = open_db(db_path)
    try:
        names = [row[0] for row in conn.execute("SELECT canonical_name FROM persons ORDER BY canonical_name")]
        organizations = [row[0] for row in conn.execute("SELECT name FROM organizations ORDER BY name")]
        fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    finally:
        conn.close()
    assert names == ["Alice Example", "Maya Chen"]
    assert organizations == ["Acme"]
    assert fact_count == 0


def test_init_no_handle_same_name_vcard_targets_self_and_warns_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "same-name.db"
    vcard_path = tmp_path / "self.vcf"
    vcard_path.write_text(
        "\n".join(
            [
                "BEGIN:VCARD",
                "VERSION:4.0",
                "FN:Maya Chen",
                "ORG:Civic Loom",
                "TITLE:Lead",
                "END:VCARD",
            ]
        ),
        encoding="utf-8",
    )

    def answer(prompt: str) -> str:
        if prompt.startswith("Canonical"):
            return "Maya Chen"
        if prompt.startswith("Email"):
            return ""
        if prompt.startswith("vCard"):
            return str(vcard_path)
        if prompt.startswith("Candidate IDs"):
            conn = open_db(db_path)
            try:
                return ",".join(row[0] for row in conn.execute("SELECT id FROM import_staging"))
            finally:
                conn.close()
        if prompt.startswith("Communication"):
            return ""
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["--db", str(db_path), "init"]) == 0
    assert "email-based self-card exclusion is unavailable" in capsys.readouterr().err
    conn = open_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0] == 1
    finally:
        conn.close()


def test_init_expands_home_in_preflighted_vcard_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    vcard_path = home / "contacts.vcf"
    vcard_path.write_text("BEGIN:VCARD\nVERSION:4.0\nFN:Alice\nEND:VCARD\n", encoding="utf-8")
    db_path = tmp_path / "expanded.db"
    monkeypatch.setenv("HOME", str(home))

    def answer(prompt: str) -> str:
        if prompt.startswith("Canonical"):
            return "Maya"
        if prompt.startswith("Email"):
            return ""
        if prompt.startswith("vCard"):
            return "~/contacts.vcf"
        if prompt.startswith("Candidate IDs"):
            conn = open_db(db_path)
            try:
                return ",".join(row[0] for row in conn.execute("SELECT id FROM import_staging"))
            finally:
                conn.close()
        if prompt.startswith("Communication"):
            return ""
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["--db", str(db_path), "init"]) == 0
    conn = open_db(db_path)
    try:
        names = [row[0] for row in conn.execute("SELECT canonical_name FROM persons ORDER BY canonical_name")]
    finally:
        conn.close()
    assert names == ["Alice", "Maya"]


def test_import_review_identifies_dependent_candidate_owners(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        ImportReviewRow(
            id="person-alice",
            source="import/vcard",
            status="pending",
            candidate={"type": "person", "name": "Alice"},
        ),
        ImportReviewRow(
            id="person-bob",
            source="import/vcard",
            status="pending",
            candidate={"type": "person", "name": "Bob"},
        ),
        ImportReviewRow(
            id="affiliation-alice",
            source="import/vcard",
            status="pending",
            candidate={
                "type": "affiliation",
                "person_candidate_id": "person-alice",
                "org": "Acme",
                "role": "Engineer",
            },
        ),
        ImportReviewRow(
            id="fact-bob",
            source="import/vcard",
            status="pending",
            candidate={
                "type": "fact",
                "person_candidate_id": "person-bob",
                "predicate": "birthday",
                "value": "1990-01-01",
            },
        ),
    ]

    cli._print_import_review(rows)

    output = capsys.readouterr().out
    assert "affiliation-alice  affiliation  Engineer at Acme — Alice (person-alice)" in output
    assert "fact-bob  fact  birthday=1990-01-01 — Bob (person-bob)" in output


def test_init_rejects_unknown_candidate_ids_without_committing_contacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "unknown-id.db"
    vcard_path = tmp_path / "contact.vcf"
    vcard_path.write_text("BEGIN:VCARD\nVERSION:4.0\nFN:Alice\nEND:VCARD\n", encoding="utf-8")
    _input_sequence(monkeypatch, ["Maya", "", str(vcard_path), "not-in-this-batch"])

    assert cli.main(["--db", str(db_path), "init"]) == 2
    conn = open_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM import_staging WHERE status = 'committed'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM user_preferences WHERE key = ?",
            (PREF_COMMUNICATION_PHILOSOPHY,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_demo_ignores_every_real_database_override_and_prints_actual_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_home = tmp_path / "xdg data"
    env_db = tmp_path / "env.db"
    config_db = tmp_path / "config.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_home = tmp_path / "config"
    config_dir = config_home / "people-context"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(f'db_path = "{config_db}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("PEOPLE_CONTEXT_DB", str(env_db))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(workspace))
    explicit_db = tmp_path / "explicit.db"

    assert cli.main(["--db", str(explicit_db), "demo", "--reset"]) == 0
    output = capsys.readouterr().out
    demo_path = (data_home / "people-context" / "demo.db").resolve()
    assert f"Demo database: {demo_path}" in output
    assert f"people-context-mcp --db {shlex.quote(str(demo_path))}" in output
    assert "resolve_person" in output
    assert "get_relationship_graph" in output
    assert "find_connection" in output
    for real_path in (explicit_db, env_db, config_db, workspace / "people-context" / "people.db"):
        assert not real_path.exists()

    conn = open_db(demo_path)
    try:
        ids = {row["canonical_name"]: row["id"] for row in conn.execute("SELECT id, canonical_name FROM persons")}
    finally:
        conn.close()
    assert ids["Amina Hassan"] in output
    assert ids["Maya Chen"] in output
    assert ids["Sofia Alvarez"] in output


def test_demo_refuses_existing_without_reset_then_reseeds_deterministic_connected_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert cli.main(["demo", "--reset"]) == 0
    capsys.readouterr()
    demo_path = tmp_path / "people-context" / "demo.db"
    assert cli.main(["demo"]) == 1
    assert "already exists" in capsys.readouterr().err

    Path(f"{demo_path}-wal").write_text("old wal", encoding="utf-8")
    Path(f"{demo_path}-shm").write_text("old shm", encoding="utf-8")
    assert cli.main(["demo", "--reset"]) == 0
    capsys.readouterr()
    assert not Path(f"{demo_path}-wal").exists()
    assert not Path(f"{demo_path}-shm").exists()

    conn = open_db(demo_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 3
        maya_id = conn.execute("SELECT id FROM persons WHERE canonical_name = 'Maya Chen'").fetchone()[0]
        sofia_id = conn.execute("SELECT id FROM persons WHERE canonical_name = 'Sofia Alvarez'").fetchone()[0]
        graph = SqliteGraphReader(conn, SystemClock())
        path = graph.path_between(maya_id, sofia_id, 4)
    finally:
        conn.close()
    assert path is not None
    assert [person.name for person in path.people] == ["Maya Chen", "Daniel Okafor", "Amina Hassan", "Sofia Alvarez"]


def test_demo_reset_replaces_symlink_without_touching_its_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    demo_path = data_home / "people-context" / "demo.db"
    demo_path.parent.mkdir(parents=True)
    real_db = tmp_path / "real.db"
    real_person = _seed(real_db, "Real Person")
    demo_path.symlink_to(real_db)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    assert cli.main(["demo", "--reset"]) == 0

    assert not demo_path.is_symlink()
    conn = open_db(real_db)
    try:
        real_names = [row[0] for row in conn.execute("SELECT canonical_name FROM persons")]
        assert SqlitePeopleRepository(conn).get(real_person.id) is not None
    finally:
        conn.close()
    assert real_names == ["Real Person"]
    conn = open_db(demo_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 4
    finally:
        conn.close()
