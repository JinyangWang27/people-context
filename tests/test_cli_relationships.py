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
