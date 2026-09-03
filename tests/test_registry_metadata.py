from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]

REGISTRY_NAMESPACE = "io.github.JinyangWang27/people-context"
SERVER_SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
PRIMARY_PACKAGE_IDENTIFIER = "people-context"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def _server_json() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def registry_package(identifier: str = PRIMARY_PACKAGE_IDENTIFIER) -> dict:
    """Return the Registry package entry with ``identifier``.

    Selecting by identifier rather than array position keeps the assertions valid if
    another package entry is ever added ahead of the primary distribution.
    """
    matches = [package for package in _server_json()["packages"] if package.get("identifier") == identifier]
    assert len(matches) == 1, f"expected exactly one {identifier!r} package entry, found {len(matches)}"
    return matches[0]


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


def test_package_version_tracks_project_version() -> None:
    """The Registry rejects a PyPI package entry without its own ``version``."""
    package = registry_package()

    assert package["version"] == _project_version() == _server_json()["version"]


def test_registry_namespace_matches_github_login_casing() -> None:
    """Namespace casing is significant: the publish grant and the PyPI ownership
    check both compare the server name byte-for-byte against the GitHub login."""
    owner = _server_json()["repository"]["url"].removeprefix("https://github.com/").split("/")[0]

    assert f"io.github.{owner}/people-context" == REGISTRY_NAMESPACE


def test_single_pypi_package_entry() -> None:
    packages = _server_json()["packages"]

    assert len(packages) == 1
    package = registry_package()
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    # The identifier matches both the primary distribution and its MCP server
    # console script, so Registry ownership and execution use the same name.
    assert package["identifier"] == "people-context"


def test_package_transport_is_valid_stdio() -> None:
    package = registry_package()

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
    package = registry_package()
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
        "people-context",
    ]


def test_pinned_primary_requirement_stays_synchronized() -> None:
    package = registry_package()
    project_version = _project_version()

    (from_argument,) = [
        argument for argument in package.get("runtimeArguments", []) if argument.get("name") == "--from"
    ]
    requirement = from_argument["value"]
    name, _, pinned_version = requirement.partition("==")
    assert name == "people-context"
    # Pin must equal the server version; bumping the release must bump this in lockstep.
    assert pinned_version == project_version == _server_json()["version"]


def test_ownership_marker_present_in_primary_distribution_readme() -> None:
    marker = f"<!-- mcp-name: {REGISTRY_NAMESPACE} -->"

    # Repository README (the primary distribution's packaged long description).
    assert marker in (ROOT / "README.md").read_text(encoding="utf-8")


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
