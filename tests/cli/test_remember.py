"""`pctx remember`: one command, one audited write set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from people_context import cli


def test_remember_creates_and_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "r.db")

    assert cli.main(["--db", db, "remember", "Alice Ng", "prefers short emails", "--org", "Acme"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Created Alice Ng (")
    assert "+ affiliation: member at Acme" in out
    assert "+ trait: communication_style: prefers short emails" in out

    assert cli.main(["--db", db, "remember", "alice ng", "moved to Berlin", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "recorded" and document["created"] is False
    assert document["recorded"][0]["summary"] == "note: moved to Berlin"


def test_ambiguous_exits_two_and_lists_candidates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "r.db")
    cli.main(["--db", db, "remember", "Priya Raman", "x"])
    cli.main(["--db", db, "remember", "Priya Shah", "x"])
    capsys.readouterr()

    assert cli.main(["--db", db, "remember", "Priya", "moved"]) == 2

    err = capsys.readouterr().err
    assert "matches several people" in err
    assert "Priya Raman" in err and "Priya Shah" in err


def test_invalid_kind_is_rejected_by_the_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--db", str(tmp_path / "r.db"), "remember", "Alice", "x", "--kind", "poem"])
