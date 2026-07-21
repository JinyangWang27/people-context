from __future__ import annotations

import json
import re
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


def test_server_version_tracks_project_version() -> None:
    server = _server_json()
    project_version = _project_version()

    # The server release tracks the application version.
    assert server["version"] == project_version


def test_single_pypi_package_entry() -> None:
    packages = _server_json()["packages"]

    assert len(packages) == 1
    package = packages[0]
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    # The identifier is the console-script command uvx executes. The server script
    # `people-context-mcp` differs from the primary distribution name, so the
    # identifier is the command and `--from people-context` installs the primary.
    assert package["identifier"] == "people-context-mcp"


def test_package_transport_is_valid_stdio() -> None:
    package = _server_json()["packages"][0]

    assert package["transport"] == {"type": "stdio"}


def _reconstruct_command(package: dict) -> list[str]:
    """Reconstruct the shell command a client assembles from the package entry.

    Clients place the package identifier between runtimeArguments and
    packageArguments: ``<runtimeHint> <runtimeArguments> <identifier> <packageArguments>``.
    """

    def tokens(arguments: list[dict]) -> list[str]:
        rendered: list[str] = []
        for argument in arguments:
            if argument["type"] == "named":
                rendered.append(argument["name"])
                if "value" in argument:
                    rendered.append(argument["value"])
            else:
                rendered.append(argument["value"])
        return rendered

    command = [package["runtimeHint"]]
    command += tokens(package.get("runtimeArguments", []))
    command.append(package["identifier"])
    command += tokens(package.get("packageArguments", []))
    return command


def test_package_reconstructs_canonical_uvx_invocation() -> None:
    package = _server_json()["packages"][0]
    project_version = _project_version()

    assert package["runtimeHint"] == "uvx"
    # The identifier is inserted as the executed command, so a redundant identifier
    # must not also appear in packageArguments.
    assert "packageArguments" not in package
    # The `--from` primary is pinned to the server version so each versioned Registry
    # entry installs a reproducible artifact instead of whatever release is latest.
    assert _reconstruct_command(package) == [
        "uvx",
        "--from",
        f"people-context=={project_version}",
        "people-context-mcp",
    ]


def test_pinned_primary_requirement_stays_synchronized() -> None:
    package = _server_json()["packages"][0]
    project_version = _project_version()

    (from_argument,) = [
        argument
        for argument in package.get("runtimeArguments", [])
        if argument.get("name") == "--from"
    ]
    requirement = from_argument["value"]
    name, _, pinned_version = requirement.partition("==")
    assert name == "people-context"
    # Pin must equal the server version; bumping the release must bump this in lockstep.
    assert pinned_version == project_version == _server_json()["version"]


def test_ownership_marker_present_in_repository_and_identifier_package_readmes() -> None:
    marker = f"<!-- mcp-name: {REGISTRY_NAMESPACE} -->"

    # Repository README (the primary distribution's packaged long description).
    assert marker in (ROOT / "README.md").read_text(encoding="utf-8")
    # The `identifier` package's packaged README, which the Registry links on PyPI.
    assert marker in (ROOT / "compat/people-context-mcp/README.md").read_text(encoding="utf-8")


def test_glama_metadata_is_well_formed() -> None:
    glama = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))

    assert glama["$schema"] == "https://glama.ai/mcp/schemas/server.json"
    assert glama["maintainers"] == ["JinyangWang27"]


def test_registry_validation_workflow_pins_the_publisher() -> None:
    workflow = (ROOT / ".github/workflows/mcp-registry-validate.yml").read_text(encoding="utf-8")

    assert 'MCP_PUBLISHER_VERSION: "v1.8.0"' in workflow
    assert "mcp-publisher validate server.json" in workflow
    # The archive is verified against a repository-pinned immutable digest, not the
    # release's own (tag-controlled) checksums file.
    match = re.search(r'MCP_PUBLISHER_SHA256:\s*"([0-9a-f]{64})"', workflow)
    assert match is not None, "workflow must pin a 64-hex SHA256 digest"
    assert "${MCP_PUBLISHER_SHA256}  ${archive}" in workflow
    assert "sha256sum --check --strict" in workflow
    # The publication docs must reuse the same pinned digest.
    docs = (ROOT / "docs/mcp-registry.md").read_text(encoding="utf-8")
    assert match.group(1) in docs


def test_registry_matrix_document_lists_every_directory() -> None:
    matrix = (ROOT / "docs/mcp-registry.md").read_text(encoding="utf-8")

    for directory in ("MCP Registry", "Smithery", "PulseMCP", "mcp.so", "Glama"):
        assert directory in matrix
    assert REGISTRY_NAMESPACE in matrix
