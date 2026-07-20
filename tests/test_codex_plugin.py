from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))


class TestCodexPluginPackage:
    """Verify the checked-in Codex plugin distribution contract."""

    def test_manifest_matches_python_package_metadata(self) -> None:
        manifest = _read_json(".codex-plugin/plugin.json")
        project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

        assert manifest["name"] == project["name"]
        assert manifest["version"] == project["version"]
        assert manifest["description"]
        assert manifest["author"]["name"] == project["authors"][0]["name"]
        assert manifest["repository"] == "https://github.com/JinyangWang27/people-context"
        assert manifest["mcpServers"] == "./.mcp.json"
        assert manifest["interface"]["displayName"] == "People Context"
        assert manifest["interface"]["websiteURL"] == manifest["repository"]

    def test_mcp_config_launches_bundled_stdio_server(self) -> None:
        server = _read_json(".mcp.json")["mcpServers"]["people-context"]

        assert server == {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--project", "${CLAUDE_PLUGIN_ROOT}", "people-context-mcp"],
        }

    def test_repo_marketplace_exposes_root_plugin(self) -> None:
        marketplace = _read_json(".agents/plugins/marketplace.json")

        assert marketplace["name"] == "people-context-plugins"
        assert marketplace["plugins"] == [
            {
                "name": "people-context",
                "source": {"source": "local", "path": "./"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        ]
