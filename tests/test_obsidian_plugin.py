"""Contract tests for the checked-in Obsidian plugin.

The plugin's own behaviour is tested with Vitest inside ``obsidian-plugin/``. What this module
guards is the part a TypeScript test cannot see: that the plugin keeps agreeing with the Python
CLI it shells out to, that its packaging stays desktop-only and lockfile-pinned, and that no
build output or sensitive-disclosure flag has crept into the committed tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from people_context.app.exports.brief import BRIEF_FORMAT, BRIEF_VERSION
from people_context.app.exports.person_index import PERSON_INDEX_FORMAT, PERSON_INDEX_VERSION
from people_context.config import DB_KEY_ENV

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "obsidian-plugin"


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads((PLUGIN_ROOT / relative_path).read_text(encoding="utf-8"))


def _source(relative_path: str) -> str:
    return (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")


class TestObsidianPluginPackaging:
    """The distribution contract: desktop-only, lockfile-pinned, nothing generated committed."""

    def test_manifest_is_desktop_only(self) -> None:
        manifest = _read_json("manifest.json")

        # The plugin starts a local process, which Obsidian mobile cannot do. Shipping without
        # this flag would offer mobile users an install that can never work.
        assert manifest["isDesktopOnly"] is True
        assert manifest["id"] == "people-context"
        assert manifest["name"] == "People Context"
        assert manifest["minAppVersion"]
        assert manifest["description"]

    def test_plugin_version_domain_is_internally_synchronized(self) -> None:
        manifest_version = _read_json("manifest.json")["version"]
        package = _read_json("package.json")
        lock = _read_json("package-lock.json")

        # The plugin keeps its own version domain, independent of the server release, so the
        # only requirement is that every file inside that domain agrees.
        assert package["version"] == manifest_version
        assert lock["version"] == manifest_version
        assert lock["packages"][""]["version"] == manifest_version

    def test_dependencies_are_locked_and_exactly_pinned(self) -> None:
        package = _read_json("package.json")

        assert "dependencies" not in package, "the plugin bundles no runtime dependency"
        assert package["devDependencies"], "build and test tooling must be declared"
        for name, specifier in package["devDependencies"].items():
            assert specifier[0].isdigit(), f"{name} must be pinned to an exact reviewed version"
        assert (PLUGIN_ROOT / "package-lock.json").is_file()

    def test_no_build_output_is_committed(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "obsidian-plugin"],
            capture_output=True,
            check=True,
            cwd=REPOSITORY_ROOT,
            text=True,
        ).stdout.split()

        # The build is reproduced and compared in CI instead of being committed, so a stale
        # bundle cannot silently diverge from the sources it claims to be built from.
        assert "obsidian-plugin/build/main.js" not in tracked
        assert not any(entry.startswith("obsidian-plugin/build/") for entry in tracked)
        assert not any(entry.startswith("obsidian-plugin/node_modules/") for entry in tracked)


class TestObsidianPluginReadContract:
    """The plugin reads exactly the two documents this repository promises to keep stable."""

    def test_commands_match_the_declared_document_formats(self) -> None:
        documents = _source("src/documents.ts")

        assert f'PERSON_INDEX_FORMAT = "{PERSON_INDEX_FORMAT}"' in documents
        assert f"PERSON_INDEX_VERSION = {PERSON_INDEX_VERSION}" in documents
        assert f'BRIEF_FORMAT = "{BRIEF_FORMAT}"' in documents
        assert f"BRIEF_VERSION = {BRIEF_VERSION}" in documents

    def test_the_cli_still_offers_the_two_subcommands_the_plugin_calls(self) -> None:
        from people_context.cli.parser import build_parser

        # `pctx list --json` and `pctx brief <person> --json` are the plugin's whole surface;
        # renaming or dropping either would break it silently at runtime.
        parser = build_parser()
        arguments = parser.parse_args(["--db", "/tmp/x.db", "--encrypted", "list", "--json"])
        assert (arguments.command, arguments.json, arguments.encrypted) == ("list", True, True)

        arguments = parser.parse_args(["brief", "01KZQXWK571FJAF03F6H63A85Z", "--json"])
        assert (arguments.command, arguments.person, arguments.json) == (
            "brief",
            "01KZQXWK571FJAF03F6H63A85Z",
            True,
        )

    def test_the_missing_key_message_is_quoted_verbatim(self) -> None:
        from people_context.config import MissingDatabaseKeyError, resolve_db_key

        with pytest.raises(MissingDatabaseKeyError) as raised:
            resolve_db_key(env={})

        # The plugin shows the CLI's own refusal rather than inventing a second wording for
        # the same condition, so the two surfaces cannot drift apart.
        assert str(raised.value) in _rendered_missing_key_message()

    def test_never_requests_sensitive_disclosure_or_deleted_people(self) -> None:
        sources = "\n".join(
            _source(f"src/{name}")
            for name in ("settings.ts", "client.ts", "bridge.ts", "documents.ts", "render.ts")
        )

        # Anything rendered into a synchronized vault has left the disclosure perimeter, so the
        # plugin must never be able to widen what it reads.
        assert '"--include-sensitive"' not in sources
        assert '"--all"' not in sources

    def test_never_stores_or_prompts_for_the_database_key(self) -> None:
        settings = _source("src/settings.ts")
        settings_tab = _source("src/settings-tab.ts")

        # The key is inherited from the process environment. A settings field for it would
        # write it into the vault's plaintext `data.json`.
        assert DB_KEY_ENV in settings
        assert "databaseKey" not in settings
        assert "databaseKey" not in settings_tab
        for field in ("executablePath", "databasePath", "encryptedDatabase", "refreshPolicy"):
            assert field in settings

    def test_subprocess_execution_is_shell_free(self) -> None:
        bridge = _source("src/bridge.ts")

        assert "shell: false" in bridge
        assert "windowsHide: true" in bridge
        assert "exec(" not in bridge, "only spawn with an argument array is permitted"
        assert "shell: true" not in bridge


def _rendered_missing_key_message() -> str:
    """Return the client's quoted copy of the CLI refusal, with its string concatenation joined."""
    client = _source("src/client.ts")
    start = client.index("export const MISSING_KEY_MESSAGE")
    end = client.index(";", client.index("fallback.", start))
    literal = client[start:end]
    return "".join(part for part in literal.split('"')[1::2])
