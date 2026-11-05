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
        assert manifest["mcpServers"] == "./.codex-plugin/mcp.json"
        assert manifest["interface"]["displayName"] == "People Context"
        assert manifest["interface"]["websiteURL"] == manifest["repository"]

    def test_mcp_config_launches_bundled_stdio_server(self) -> None:
        # Codex 0.166 neither expands `${...}` in args nor sets a plugin-root variable, but it does
        # resolve a relative `cwd` against the plugin root. A literal variable here starts nothing.
        server = _read_json(".codex-plugin/mcp.json")["mcpServers"]["people-context"]

        assert server == {
            "type": "stdio",
            "command": "uv",
            "cwd": ".",
            "args": ["run", "--no-dev", "--project", ".", "people-context-mcp", "--tools", "core"],
            "env_vars": ["PEOPLE_CONTEXT_DB", "PEOPLE_CONTEXT_DB_KEY"],
            "startup_timeout_sec": 120,
        }

    def test_claude_plugin_config_uses_the_exact_plugin_variable(self) -> None:
        # The plugin loader substitutes only the exact `${CLAUDE_PLUGIN_ROOT}`: the
        # `${CLAUDE_PLUGIN_ROOT:-.}` form silently becomes the user's working directory.
        manifest = _read_json(".claude-plugin/plugin.json")
        server = _read_json(".claude-plugin/mcp.json")["mcpServers"]["people-context"]

        assert manifest["mcpServers"] == "./.claude-plugin/mcp.json"
        assert server == {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--no-dev", "--project", "${CLAUDE_PLUGIN_ROOT}", "people-context-mcp", "--tools", "core"],
        }

    def test_root_config_starts_from_a_plain_checkout(self) -> None:
        # Claude Code loads this file as project config, where no plugin variable is set: a
        # variable here makes `uv` exit on a directory literally named `${CLAUDE_PLUGIN_ROOT}`.
        server = _read_json(".mcp.json")["mcpServers"]["people-context"]

        assert server == {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--no-dev", "--project", ".", "people-context-mcp"],
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
