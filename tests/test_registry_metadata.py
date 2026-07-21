from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]

REGISTRY_NAMESPACE = "io.github.jinyangwang27/people-context"
SERVER_SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def _server_json() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_server_json_uses_pinned_schema_and_recorded_namespace() -> None:
    server = _server_json()

    assert server["$schema"] == SERVER_SCHEMA_URL
    assert server["name"] == REGISTRY_NAMESPACE
    assert server["description"]
    assert server["repository"] == {
        "url": "https://github.com/JinyangWang27/people-context",
        "source": "github",
        "id": "R_kgDOTan0Jg",
    }


def test_server_and_package_versions_match_project_version() -> None:
    server = _server_json()
    project_version = _project_version()

    assert server["version"] == project_version

    packages = server["packages"]
    assert len(packages) == 1
    package = packages[0]
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    assert package["identifier"] == "people-context"
    assert package["version"] == project_version


def test_package_transport_is_valid_stdio() -> None:
    package = _server_json()["packages"][0]

    assert package["transport"] == {"type": "stdio"}


def test_package_reconstructs_canonical_uvx_invocation() -> None:
    package = _server_json()["packages"][0]

    assert package["runtimeHint"] == "uvx"
    assert package["runtimeArguments"] == [{"type": "named", "name": "--from", "value": "people-context"}]
    assert package["packageArguments"] == [{"type": "positional", "value": "people-context-mcp"}]


def test_packaged_readme_carries_ownership_marker() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"<!-- mcp-name: {REGISTRY_NAMESPACE} -->" in readme


def test_glama_metadata_is_well_formed() -> None:
    glama = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))

    assert glama["$schema"] == "https://glama.ai/mcp/schemas/server.json"
    assert glama["maintainers"] == ["JinyangWang27"]


def test_registry_validation_workflow_pins_the_publisher() -> None:
    workflow = (ROOT / ".github/workflows/mcp-registry-validate.yml").read_text(encoding="utf-8")

    assert 'MCP_PUBLISHER_VERSION: "v1.8.0"' in workflow
    assert "mcp-publisher validate server.json" in workflow
    assert "sha256sum --check --strict" in workflow


def test_registry_matrix_document_lists_every_directory() -> None:
    matrix = (ROOT / "docs/mcp-registry.md").read_text(encoding="utf-8")

    for directory in ("MCP Registry", "Smithery", "PulseMCP", "mcp.so", "Glama"):
        assert directory in matrix
    assert REGISTRY_NAMESPACE in matrix
