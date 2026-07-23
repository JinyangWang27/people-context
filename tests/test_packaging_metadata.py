from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
CURRENT_RELEASE_VERSION = "0.2.0"
COMPATIBILITY_RELEASE_VERSION = "0.1.0.post2"


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def test_reviewed_release_versions_are_synchronized() -> None:
    primary_version = _toml(ROOT / "pyproject.toml")["project"]["version"]
    compatibility_version = _toml(ROOT / "compat/people-context-mcp/pyproject.toml")["project"]["version"]
    claude_plugin_version = _json(ROOT / ".claude-plugin/plugin.json")["version"]
    claude_marketplace_version = _json(ROOT / ".claude-plugin/marketplace.json")["plugins"][0]["version"]
    openclaw_package_version = _json(ROOT / "openclaw-plugin/package.json")["version"]
    openclaw_manifest_version = _json(ROOT / "openclaw-plugin/openclaw.plugin.json")["version"]
    openclaw_lock = _json(ROOT / "openclaw-plugin/package-lock.json")

    assert primary_version == CURRENT_RELEASE_VERSION
    assert compatibility_version == COMPATIBILITY_RELEASE_VERSION
    assert claude_plugin_version == claude_marketplace_version == CURRENT_RELEASE_VERSION
    assert openclaw_package_version == openclaw_manifest_version == CURRENT_RELEASE_VERSION
    assert openclaw_lock["version"] == CURRENT_RELEASE_VERSION
    assert openclaw_lock["packages"][""]["version"] == CURRENT_RELEASE_VERSION

    client_version = f'version: "{CURRENT_RELEASE_VERSION}"'
    assert client_version in (ROOT / "openclaw-plugin/src/index.ts").read_text(encoding="utf-8")
    assert client_version in (ROOT / "openclaw-plugin/dist/index.js").read_text(encoding="utf-8")

    packed_artifact = f"openclaw-plugin-people-context-{CURRENT_RELEASE_VERSION}.tgz"
    for guide in ("docs/openclaw-plugin.md", "openclaw-plugin/README.md"):
        assert packed_artifact in (ROOT / guide).read_text(encoding="utf-8")


def test_current_changelog_covers_recent_user_facing_capabilities() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for capability in (
        "ICS calendar attendee imports",
        "LinkedIn Connections CSV imports",
        "`people-context init`",
        "`people-context demo [--reset]`",
        "packaged usage skill",
        "`/people-context:who`",
        "`/people-context:remember`",
        "`/people-context:reminders`",
    ):
        assert capability in changelog
