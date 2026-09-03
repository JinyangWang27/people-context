from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from people_context import __version__ as package_version
from tests.test_registry_metadata import registry_package

ROOT = Path(__file__).parents[1]
PRIMARY_RELEASE_VERSION = "1.1.0"  # x-release-please-version
INTEGRATION_RELEASE_VERSION = "0.2.0"
#: The MCPB manifest schema version is tooling metadata, never the application release.
MCPB_MANIFEST_SCHEMA_VERSION = "0.4"


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _locked_root_version() -> str:
    """Return the ``people-context`` version recorded in the root ``uv.lock``."""
    editable = [
        package
        for package in _toml(ROOT / "uv.lock")["package"]
        if package["name"] == "people-context" and package.get("source", {}).get("editable") == "."
    ]
    assert len(editable) == 1, "uv.lock must record exactly one editable root project entry"
    return editable[0]["version"]


def _registry_requirement_pin() -> str:
    """Return the version pinned by the Registry package's ``--from`` runtime argument."""
    (from_argument,) = [
        argument for argument in registry_package().get("runtimeArguments", []) if argument.get("name") == "--from"
    ]
    name, separator, pinned = from_argument["value"].partition("==")
    assert (name, separator) == ("people-context", "=="), "the Registry primary must be pinned with =="
    return pinned


def _bundle_dependency_pin() -> str:
    """Return the version pinned by the MCPB bundle's ``people-context`` dependency."""
    (requirement,) = _toml(ROOT / "mcpb/pyproject.toml")["project"]["dependencies"]
    name, separator, pinned = requirement.partition("==")
    assert (name, separator) == ("people-context", "=="), "the bundle dependency must be pinned with =="
    return pinned


def test_primary_distribution_uses_new_name_and_stable_entrypoints() -> None:
    project = _toml(ROOT / "pyproject.toml")["project"]

    assert project["name"] == "people-context"
    assert project["scripts"] == {
        "people-context-mcp": "people_context.adapters.mcp.server:main",
        "people-context": "people_context.adapters.mcp.server:main",
        "pctx": "people_context.cli:main",
    }


def test_primary_distribution_requires_mcp_v2() -> None:
    project = _toml(ROOT / "pyproject.toml")["project"]

    assert "mcp>=2,<3" in project["dependencies"]


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


def test_server_distribution_versions_are_synchronized() -> None:
    """Every surface that names a server release moves together with the root project."""
    primary_version = _toml(ROOT / "pyproject.toml")["project"]["version"]

    # The five semantic server-release values a 1.0 (or any) release must agree on.
    assert _json(ROOT / "server.json")["version"] == primary_version
    assert _registry_requirement_pin() == primary_version
    assert _json(ROOT / "mcpb/manifest.json")["version"] == primary_version
    assert _bundle_dependency_pin() == primary_version
    assert _toml(ROOT / "mcpb/pyproject.toml")["project"]["version"] == primary_version

    # The importable package and the locked root project follow the same value, so
    # `uv lock --check` and a built wheel cannot disagree with the declared release.
    assert package_version == primary_version
    assert _locked_root_version() == primary_version


def test_mcpb_manifest_schema_version_is_not_the_release_version() -> None:
    """`manifest_version` is the MCPB schema version and must stay an independent field."""
    manifest = _json(ROOT / "mcpb/manifest.json")
    primary_version = _toml(ROOT / "pyproject.toml")["project"]["version"]

    assert manifest["manifest_version"] == MCPB_MANIFEST_SCHEMA_VERSION
    assert manifest["manifest_version"] != primary_version


def test_release_readiness_declares_production_stable() -> None:
    """The 1.0 metadata synchronization also retires the pre-1.0 Alpha classifier."""
    classifiers = _toml(ROOT / "pyproject.toml")["project"]["classifiers"]

    assert "Development Status :: 5 - Production/Stable" in classifiers
    development_status = [item for item in classifiers if item.startswith("Development Status ::")]
    assert development_status == ["Development Status :: 5 - Production/Stable"]


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


def _encrypted_extra() -> list[str]:
    return _toml(ROOT / "pyproject.toml")["project"]["optional-dependencies"]["encrypted"]


def test_encrypted_extra_pins_a_reviewed_range_on_its_supported_platform() -> None:
    """`sqlcipher3-binary` ships manylinux x86_64 wheels only and no sdist.

    The environment marker keeps resolution working on every other platform
    instead of shipping an extra that cannot install there.
    """
    (requirement,) = _encrypted_extra()
    specifier, _, marker = requirement.partition(";")

    assert specifier.strip() == "sqlcipher3-binary>=0.6.0,<0.7"
    assert marker.strip() == "sys_platform == 'linux' and platform_machine == 'x86_64'"


def test_encrypted_binding_stays_out_of_the_default_development_environment() -> None:
    """`uv sync` must not fail where the manylinux wheel cannot install.

    PEP 508 has no libc marker, so the extra's marker also matches musl-based
    Linux x86_64 (Alpine), where no compatible artifact exists. Keeping the
    binding out of the default dev group means the documented `uv sync` command
    never breaks there; only an explicit opt-in can.
    """
    dev_group = _toml(ROOT / "pyproject.toml")["dependency-groups"]["dev"]

    assert all("sqlcipher" not in requirement for requirement in dev_group)


def test_locked_state_records_the_encrypted_binding() -> None:
    locked = {package["name"] for package in _toml(ROOT / "uv.lock")["package"]}

    assert "sqlcipher3-binary" in locked, "changing optional dependencies requires committing uv.lock"


def test_encryption_stays_opt_in_and_off_by_default() -> None:
    """Nothing in the shipped metadata makes encryption a runtime dependency."""
    project = _toml(ROOT / "pyproject.toml")["project"]

    assert all("sqlcipher" not in requirement for requirement in project["dependencies"])


def test_ci_fails_loudly_when_the_encrypted_extra_is_missing() -> None:
    """A locked all-extras install must be proven, not assumed.

    The encryption tests skip when the binding is absent, so CI needs an explicit
    import check; otherwise a resolution or marker mistake would look like a pass.
    """
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    install_index = workflow.index("uv sync --locked --all-extras")
    verify_index = workflow.index('uv run --locked python -c "import sqlcipher3"')
    assert install_index < verify_index, "the import check must run after the locked install"
