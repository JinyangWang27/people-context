from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
RELEASE_PLEASE_ACTION_SHA = "45996ed1f6d02564a971a2fa1b5860e934307cf7"
RELEASE_PR_WORKFLOWS = {
    "ci.yml",
    "codeql.yml",
    "claude-plugin-validate.yml",
    "mcp-registry-validate.yml",
    "codex-plugin-validate.yml",
    "mcpb-validate.yml",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_release_manifest_tracks_the_primary_distribution() -> None:
    manifest = _json(ROOT / ".release-please-manifest.json")
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project_version = tomllib.load(stream)["project"]["version"]

    assert manifest == {".": project_version}


def test_release_config_updates_all_coupled_primary_versions() -> None:
    config = _json(ROOT / "release-please-config.json")

    assert config["release-type"] == "python"
    assert config["include-component-in-tag"] is False
    assert config["include-v-in-tag"] is True
    assert config["bump-minor-pre-major"] is True

    package = config["packages"]["."]
    assert package["package-name"] == "people-context"

    entries = {
        (entry["type"], entry["path"], entry.get("jsonpath")) for entry in package["extra-files"]
    }
    assert entries == {
        ("json", "server.json", "$.version"),
        ("json", "server.json", "$.packages[0].runtimeArguments[0].value"),
        ("json", "mcpb/manifest.json", "$.version"),
        ("json", ".codex-plugin/plugin.json", "$.version"),
        ("generic", "mcpb/pyproject.toml", None),
        ("toml", "uv.lock", '$.package[?(@.name=="people-context")].version'),
        ("generic", "tests/test_packaging_metadata.py", None),
        ("generic", "docs/mcp-registry.md", None),
    }

    server = _json(ROOT / "server.json")
    requirement = server["packages"][0]["runtimeArguments"][0]["value"]
    assert requirement == f"people-context=={server['version']}"


def test_release_workflow_uses_pinned_action_and_tag_dispatch() -> None:
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
    publish_workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert f"googleapis/release-please-action@{RELEASE_PLEASE_ACTION_SHA}" in workflow
    assert "actions: write" in workflow
    assert "contents: write" in workflow
    assert "pull-requests: write" in workflow
    assert "steps.release.outputs.release_created == 'true'" in workflow
    assert 'gh workflow run release.yml --repo "$GITHUB_REPOSITORY" --ref "$TAG_NAME"' in workflow
    assert 'gh workflow run docker-publish.yml --repo "$GITHUB_REPOSITORY" --ref "$TAG_NAME"' in workflow

    assert "workflow_dispatch:" in publish_workflow
    assert "needs: verify-tag" in publish_workflow
    assert "refs/tags/v*" in publish_workflow


def test_release_workflow_dispatches_suppressed_pull_request_checks() -> None:
    release_workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")

    assert "steps.release.outputs.prs_created == 'true'" in release_workflow
    assert "fromJSON(steps.release.outputs.pr).headBranchName" in release_workflow
    for workflow_name in RELEASE_PR_WORKFLOWS:
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        assert "workflow_dispatch:" in workflow
        assert f'gh workflow run {workflow_name} --repo "$GITHUB_REPOSITORY" --ref "$PR_BRANCH"' in release_workflow


def test_docker_dispatch_requires_a_release_tag() -> None:
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert 'if [[ "$RELEASE_REF" != refs/tags/v* ]]' in workflow


def test_manual_release_retry_tolerates_existing_pypi_artifacts() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "skip-existing: ${{ github.event_name == 'workflow_dispatch' }}" in workflow


def test_generic_updater_markers_cover_non_structured_versions() -> None:
    bundle_project = (ROOT / "mcpb/pyproject.toml").read_text(encoding="utf-8")
    packaging_test = (ROOT / "tests/test_packaging_metadata.py").read_text(encoding="utf-8")
    registry_docs = (ROOT / "docs/mcp-registry.md").read_text(encoding="utf-8")

    assert bundle_project.count("x-release-please-version") == 2
    assert "PRIMARY_RELEASE_VERSION" in packaging_test
    assert "x-release-please-version" in packaging_test
    assert "x-release-please-start-version" in registry_docs
    assert "x-release-please-end" in registry_docs
