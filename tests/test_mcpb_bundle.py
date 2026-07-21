from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
MCPB = ROOT / "mcpb"

# Exact reviewed MCPB CLI release the build tooling and workflows pin. Bumping it
# is a reviewed change in mcpb/build.sh, this test, and the workflows below.
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
# Reviewed MCPB manifest schema version that introduces the native-UV server type.
# This is the tooling's schema version and is deliberately independent of the
# application release version.
MANIFEST_SCHEMA_VERSION = "0.4"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def _manifest() -> dict:
    return json.loads((MCPB / "manifest.json").read_text(encoding="utf-8"))


def _bundle_pyproject() -> dict:
    with (MCPB / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_manifest_declares_native_uv_server() -> None:
    server = _manifest()["server"]

    assert server["type"] == "uv"
    assert server["entry_point"] == "server/main.py"
    # mcp_config runs the bundled entry point with the host UV runtime against the
    # bundle project directory; the CLI requires it even for the uv server type.
    assert server["mcp_config"] == {
        "command": "uv",
        "args": ["run", "--directory", "${__dirname}", "server/main.py"],
    }


def test_manifest_schema_version_is_independent_of_the_release() -> None:
    # The schema manifest_version is the reviewed MCPB tooling version, not the
    # application release version. Keep the two concepts separate.
    assert _manifest()["manifest_version"] == MANIFEST_SCHEMA_VERSION


def test_manifest_semantic_version_tracks_the_release() -> None:
    manifest = _manifest()
    project_version = _project_version()

    assert manifest["version"] == project_version
    assert manifest["name"] == "people-context"
    assert manifest["display_name"] == "People Context"
    assert manifest["description"]
    assert manifest["author"]["name"] == "Jinyang Wang"
    assert manifest["license"] == "MIT"


def test_manifest_carries_a_local_permission_warning() -> None:
    long_description = _manifest()["long_description"].lower()

    # The bundle runs local Python with the user's filesystem permissions and is
    # not a sandbox; the manifest must say so.
    assert "not a sandbox" in long_description
    assert "filesystem permissions" in long_description


def test_bundle_pyproject_pins_matching_release_and_is_not_a_package() -> None:
    pyproject = _bundle_pyproject()
    project_version = _project_version()

    assert pyproject["project"]["dependencies"] == [f"people-context=={project_version}"]
    # The host UV runtime installs only the dependency; the bundle is not itself a
    # distributable package.
    assert pyproject["tool"]["uv"]["package"] is False


def test_entry_point_delegates_to_the_packaged_server() -> None:
    source = (MCPB / "server" / "main.py").read_text(encoding="utf-8")

    assert "from people_context.adapters.mcp.server import main" in source
    assert "main()" in source


def test_mcpbignore_excludes_tooling_and_transient_state() -> None:
    ignore = (MCPB / ".mcpbignore").read_text(encoding="utf-8")

    for pattern in ("build.sh", "README.md", "dist/", ".venv/", "*.mcpb"):
        assert pattern in ignore


def test_build_script_pins_the_exact_reviewed_cli() -> None:
    build = (MCPB / "build.sh").read_text(encoding="utf-8")

    assert 'MCPB_CLI_VERSION="2.1.2"' in build
    assert "mcpb validate" in build
    assert "mcpb pack" in build
    # Archive-content inspection.
    assert "unzip -l" in build
    # No floating latest.
    assert "@latest" not in build


def test_validate_workflow_runs_pinned_build_and_metadata_test() -> None:
    workflow = (ROOT / ".github/workflows/mcpb-validate.yml").read_text(encoding="utf-8")

    assert "tests/test_mcpb_bundle.py" in workflow
    assert "mcpb/build.sh" in workflow


def test_release_workflow_attaches_the_bundle() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "mcpb/build.sh" in workflow
    assert "gh release upload" in workflow
    # The upload job takes least-privilege write scope only where it is needed.
    assert "contents: write" in workflow
    # The bundle pins people-context==<release>, so it must be attached only after
    # the primary PyPI publication succeeds — not in parallel with it — otherwise a
    # downloaded bundle fails to install until (or unless) that version is on PyPI.
    assert "needs: publish" in workflow
