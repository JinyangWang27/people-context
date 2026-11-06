"""CLI coverage for M7 relationship vocabulary and normalization commands."""

from __future__ import annotations

from pathlib import Path

from people_context.cli import main


def test_relationship_types_list_and_add_are_persisted_and_audited(tmp_path: Path, capsys) -> None:
    db = tmp_path / "people.db"

    assert main(["--db", str(db), "relationship-types"]) == 0
    listed = capsys.readouterr().out
    assert "reports_to" in listed
    assert "Uncategorized types in use" in listed

    assert (
        main(
            [
                "--db",
                str(db),
                "relationship-types",
                "add",
                "co_founder_of",
                "--category",
                "professional",
                "--symmetric",
                "--synonym",
                "cofounder",
            ]
        )
        == 0
    )
    assert "Added relationship vocabulary: co_founder_of" in capsys.readouterr().out

    assert main(["--db", str(db), "sync-log", "--payloads"]) == 0
    sync = capsys.readouterr().out
    assert "relationship_type:co_founder_of" in sync
    assert "cofounder" in sync


def test_normalize_relationships_is_dry_run_by_default(tmp_path: Path, capsys) -> None:
    db = tmp_path / "people.db"
    assert main(["--db", str(db), "normalize-relationships"]) == 0
    assert "No relationship normalization changes" in capsys.readouterr().out


def _people(db: Path, *names: str) -> None:
    for name in names:
        assert main(["--db", str(db), "remember", name, "x"]) == 0


def _edges(db: Path) -> list[tuple[str, str, str, str | None, str | None]]:
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            """SELECT s.canonical_name, r.type, o.canonical_name, r.label, r.valid_from
               FROM relationships r JOIN persons s ON s.id = r.subject_id JOIN persons o ON o.id = r.object_id
               ORDER BY s.canonical_name"""
        ).fetchall()
    finally:
        conn.close()


def test_relate_links_several_people_to_one_and_stores_the_canonical_direction(tmp_path: Path, capsys) -> None:
    db = tmp_path / "people.db"
    _people(db, "Dana Ito", "Eli Park", "Pat Lee")
    capsys.readouterr()

    team = ["relate", "Eli Park", "Pat Lee", "reports_to", "Dana Ito", "--from", "2024-01-02"]
    assert main(["--db", str(db), *team]) == 0
    out = capsys.readouterr().out
    assert "Eli Park reports_to Dana Ito" in out and "Pat Lee reports_to Dana Ito" in out

    # `manages` is stored as the inverse `reports_to`, and a repeat updates the existing edge in place.
    assert main(["--db", str(db), "relate", "Dana Ito", "manages", "Eli Park", "--label", "team lead"]) == 0
    assert _edges(db) == [
        ("Eli Park", "reports_to", "Dana Ito", "team lead", "2024-01-02"),
        ("Pat Lee", "reports_to", "Dana Ito", None, "2024-01-02"),
    ]


def test_relate_writes_nothing_when_any_name_does_not_resolve(tmp_path: Path, capsys) -> None:
    db = tmp_path / "people.db"
    _people(db, "Dana Ito", "Eli Park", "Priya Raman", "Priya Shah")
    capsys.readouterr()

    assert main(["--db", str(db), "relate", "Eli Park", "Nobody Here", "reports_to", "Dana Ito"]) == 1
    assert main(["--db", str(db), "relate", "Eli Park", "Priya", "reports_to", "Dana Ito"]) == 2
    assert "Priya Raman" in capsys.readouterr().err
    assert _edges(db) == []


def test_relate_refuses_a_self_link_and_a_bad_date(tmp_path: Path, capsys) -> None:
    db = tmp_path / "people.db"
    _people(db, "Dana Ito", "Eli Park")
    capsys.readouterr()

    assert main(["--db", str(db), "relate", "Dana Ito", "colleague_of", "Dana Ito"]) == 1
    assert main(["--db", str(db), "relate", "Eli Park", "reports_to", "Dana Ito", "--from", "soon"]) == 1
    inverted = ["--from", "2025-01-01", "--to", "2024-01-01"]
    assert main(["--db", str(db), "relate", "Eli Park", "reports_to", "Dana Ito", *inverted]) == 1
    assert _edges(db) == []
