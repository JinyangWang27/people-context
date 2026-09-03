"""`pctx remember`: one command, one audited write set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import people_context
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


def test_json_mode_reports_the_same_exit_codes_as_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Automation reading --json must tell an unresolved name (2) from any other refusal (1)."""
    db = str(tmp_path / "r.db")
    cli.main(["--db", db, "remember", "Priya Raman", "x"])
    cli.main(["--db", db, "remember", "Priya Shah", "x"])
    capsys.readouterr()

    assert cli.main(["--db", db, "remember", "Priya", "moved", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "ambiguous"

    assert cli.main(["--db", db, "remember", "Dana Ito", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "nothing_to_record"

    assert cli.main(["--db", db, "remember", "Dana Ito", "x", "--kind", "affiliation", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid_request"


def test_version_flag_reports_the_installed_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """The bug report form asks for this; argparse exits 0 from a version action."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"pctx {people_context.__version__}"


def test_an_unparseable_occurred_at_is_a_usage_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--db", str(tmp_path / "r.db"), "remember", "Alice", "met", "--occurred-at", "not-a-date"])

    assert exit_info.value.code == 2
    assert "is not an ISO 8601 date" in capsys.readouterr().err


def test_a_valid_occurred_at_dates_the_interaction(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "r.db")

    assert cli.main(["--db", db, "remember", "Alice", "met Alice yesterday", "--occurred-at", "2026-08-20"]) == 0
    assert "+ interaction:" in capsys.readouterr().out

    assert cli.main(["--db", db, "timeline", "Alice"]) == 0
    assert "2026-08-20" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["   ", "\t"])
def test_a_blank_name_is_a_usage_error_not_a_pydantic_dump(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """`remember` is the one person-addressed command that builds a validated model."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--db", str(tmp_path / "r.db"), "remember", name, "moved to Berlin"])

    err = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert "a person name is required" in err
    assert "pydantic" not in err


def test_a_padded_name_is_trimmed_and_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(tmp_path / "r.db"), "remember", "  Alice Ng  ", "moved to Berlin"]) == 0

    assert capsys.readouterr().out.startswith("Created Alice Ng (")
