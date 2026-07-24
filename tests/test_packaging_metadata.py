from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from people_context import __version__ as package_version

ROOT = Path(__file__).parents[1]
PRIMARY_RELEASE_VERSION = "0.3.0"  # x-release-please-version
INTEGRATION_RELEASE_VERSION = "0.2.0"


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
        "people-context": "people_context.adapters.mcp.server:main",
        "pctx": "people_context.cli:main",
    }


def test_release_workflow_targets_only_primary_project() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "https://pypi.org/p/people-context\n" in workflow
    assert "https://pypi.org/p/people-context-mcp\n" not in workflow
    assert "dist-legacy" not in workflow


def test_reviewed_release_versions_are_synchronized() -> None:
    primary_version = _toml(ROOT / "pyproject.toml")["project"]["version"]
    claude_plugin_version = _json(ROOT / ".claude-plugin/plugin.json")["version"]
    claude_marketplace_version = _json(ROOT / ".claude-plugin/marketplace.json")["plugins"][0]["version"]
    openclaw_package_version = _json(ROOT / "openclaw-plugin/package.json")["version"]
    openclaw_manifest_version = _json(ROOT / "openclaw-plugin/openclaw.plugin.json")["version"]
    openclaw_lock = _json(ROOT / "openclaw-plugin/package-lock.json")

    assert primary_version == PRIMARY_RELEASE_VERSION
    assert package_version == primary_version
    assert claude_plugin_version == claude_marketplace_version == INTEGRATION_RELEASE_VERSION
    assert openclaw_package_version == openclaw_manifest_version == INTEGRATION_RELEASE_VERSION
    assert openclaw_lock["version"] == INTEGRATION_RELEASE_VERSION
    assert openclaw_lock["packages"][""]["version"] == INTEGRATION_RELEASE_VERSION

    client_version = f'version: "{INTEGRATION_RELEASE_VERSION}"'
    assert client_version in (ROOT / "openclaw-plugin/src/index.ts").read_text(encoding="utf-8")
    assert client_version in (ROOT / "openclaw-plugin/dist/index.js").read_text(encoding="utf-8")

    packed_artifact = f"openclaw-plugin-people-context-{INTEGRATION_RELEASE_VERSION}.tgz"
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
