from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_primary_distribution_uses_new_name_and_stable_entrypoints() -> None:
    project = _toml(ROOT / "pyproject.toml")["project"]

    assert project["name"] == "people-context"
    assert project["scripts"] == {
        "people-context-mcp": "people_context.adapters.mcp.server:main",
        "people-context": "people_context.cli:main",
    }


def test_legacy_distribution_is_dependency_only_compatibility_package() -> None:
    project = _toml(ROOT / "compat/people-context-mcp/pyproject.toml")["project"]

    assert project["name"] == "people-context-mcp"
    assert project["dependencies"] == ["people-context>=0.1.0"]
    assert project["scripts"]["people-context-mcp"] == "people_context.adapters.mcp.server:main"
    assert project["scripts"]["people-context"] == "people_context.cli:main"


def test_release_workflow_targets_primary_and_legacy_projects() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "https://pypi.org/p/people-context\n" in workflow
    assert "https://pypi.org/p/people-context-mcp\n" in workflow
    assert "skip-existing: true" in workflow
