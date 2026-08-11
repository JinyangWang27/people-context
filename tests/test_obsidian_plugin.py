"""Contract tests for the checked-in Obsidian plugin.

The plugin's own behaviour is tested with Vitest inside ``obsidian-plugin/``. What this module
guards is the part a TypeScript test cannot see: that the plugin keeps agreeing with the Python
CLI it shells out to, that its packaging stays desktop-only and lockfile-pinned, and that no
build output or sensitive-disclosure flag has crept into the committed tree.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
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


def _pyproject() -> dict[str, Any]:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


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

    def test_release_requires_a_plugin_tag_for_every_event(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/obsidian-plugin-release.yml").read_text(
            encoding="utf-8"
        )

        # The mirror job replaces the distribution repository's tree and cuts a release from
        # it, so a branch dispatch must not be able to publish untagged code. The tag gate is
        # therefore unconditional rather than limited to `push`.
        assert "refs/tags/obsidian-plugin-v*" in workflow
        assert "if: github.event_name == 'push'" not in workflow
        gate = workflow.index("Require a plugin tag matching the manifest version")
        install = workflow.index("Install dependencies from the committed lockfile")
        assert gate < install, "the tag gate must run before anything is built"

    def test_mirror_release_is_retryable(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/obsidian-plugin-release.yml").read_text(
            encoding="utf-8"
        )

        # The workflow is dispatchable from an existing tag, so a retry after a partially
        # completed mirror must finish rather than fail on the release it created itself.
        assert "gh release view" in workflow
        assert "gh release upload" in workflow
        assert "--clobber" in workflow

    def test_encrypted_prerequisite_names_the_extra_that_actually_ships_sqlcipher(self) -> None:
        project = _pyproject()["project"]
        extras = project["optional-dependencies"]

        # The guides tell users to install with this extra; if it were renamed or its contents
        # moved into the base dependencies, that instruction would silently become wrong.
        assert "encrypted" in extras
        assert any(requirement.startswith("sqlcipher3-binary") for requirement in extras["encrypted"])
        assert not any(
            requirement.startswith("sqlcipher3") for requirement in project["dependencies"]
        ), "SQLCipher is opt-in; the encrypted docs exist because it is not a base dependency"

        for guide in ("docs/obsidian-plugin.md", "obsidian-plugin/README.md"):
            text = (REPOSITORY_ROOT / guide).read_text(encoding="utf-8")
            assert "people-context[encrypted]" in text

    def test_brief_resolves_a_missing_id_by_name_which_is_why_the_plugin_checks_identity(
        self,
    ) -> None:
        people = (REPOSITORY_ROOT / "src/people_context/cli/people.py").read_text(encoding="utf-8")
        client = _source("src/client.ts")

        # `resolve_person` tries the reference as an id and then falls back to name resolution,
        # so a stale row can answer with a different, still-active person. The plugin therefore
        # verifies the returned document describes the person it asked for.
        assert "result = runtime.use_cases.resolve_person.execute(reference)" in people
        assert "document.person.id !== personId" in client
        assert "PersonIdentityError" in client

    def test_subprocess_execution_is_shell_free(self) -> None:
        bridge = _source("src/bridge.ts")

        assert "shell: false" in bridge
        assert "windowsHide: true" in bridge
        assert "exec(" not in bridge, "only spawn with an argument array is permitted"
        assert "shell: true" not in bridge

    def test_termination_is_tree_aware_on_both_platform_families(self) -> None:
        bridge = _source("src/bridge.ts")

        # `pctx` is a console-script launcher, so the process holding the database may be a
        # child of the process the plugin spawned. A single signal to the direct child would
        # report a stopped run while leaving that child alive.
        assert "taskkill" in bridge, "Windows has no process groups; the tree needs taskkill"
        assert '"/t"' in bridge, "taskkill without /t kills only the launcher"
        assert "-pid" in bridge or "hooks.kill(-pid" in bridge

        # A taskkill that cannot launch is reported as an asynchronous `error` event, not
        # thrown. Without a listener that event is an uncaught exception in the host process,
        # and the original tree keeps running with no fallback.
        assert 'killer.on("error"' in bridge
        assert 'killer.on("close"' in bridge


def _rendered_missing_key_message() -> str:
    """Return the client's quoted copy of the CLI refusal, with its string concatenation joined."""
    client = _source("src/client.ts")
    start = client.index("export const MISSING_KEY_MESSAGE")
    end = client.index(";", client.index("fallback.", start))
    literal = client[start:end]
    return "".join(part for part in literal.split('"')[1::2])
