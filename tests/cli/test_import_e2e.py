"""An installed `pctx` round trip through stage, review, commit, and list."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_URL_SENTINEL = "LINKEDIN-URL-MUST-NOT-LEAK-41d7"


def test_installed_cli_stages_reviews_commits_then_lists_the_imported_people(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[2]
    db_path = tmp_path / "people.db"
    source = tmp_path / "connections.csv"
    source.write_text(
        "\n".join(
            [
                _HEADERS,
                f"Amina,Haddad,{_URL_SENTINEL},amina@example.com,Acme,Engineer,22 Jul 2026,note",
                f"Sofia,Rossi,{_URL_SENTINEL},sofia@example.com,Globex,Designer,23 Jul 2026,note",
            ]
        ),
        encoding="utf-8",
    )

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [uv, "run", "pctx", "--db", str(db_path), *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        return completed

    staged = json.loads(run("import", "stage", "linkedin", str(source), "--json").stdout)
    assert staged["format"] == "people-context-import-batch"
    assert staged["candidate_count"] == 6

    reviewed = json.loads(run("import", "review", staged["batch_id"], "--json").stdout)
    assert reviewed["format"] == "people-context-import-review"
    assert [row["status"] for row in reviewed["candidates"]] == ["pending"] * 6

    committed = json.loads(run("import", "commit", staged["batch_id"], "--all", "--json").stdout)
    assert committed["format"] == "people-context-import-commit"
    assert len(committed["committed_ids"]) == 6
    assert committed["unresolved_ids"] == []

    listed = json.loads(run("list", "--json").stdout)
    assert [entry["canonical_name"] for entry in listed["people"]] == ["Amina Haddad", "Sofia Rossi"]
    assert _URL_SENTINEL not in json.dumps(listed)
    assert _URL_SENTINEL not in json.dumps(reviewed)
