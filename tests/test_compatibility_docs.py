"""Checks that the compatibility promise stays present, linked, and true to shipped code."""

from __future__ import annotations

import re
from pathlib import Path

from people_context.app.exports.json import ExportDocument
from people_context.domain.sync_bundle import SYNC_BUNDLE_FORMAT, SYNC_BUNDLE_VERSION

ROOT = Path(__file__).parents[1]
COMPATIBILITY = ROOT / "docs/compatibility.md"

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_compatibility_document_exists_and_covers_every_promised_surface() -> None:
    document = _read("docs/compatibility.md")

    for heading in (
        "# Compatibility promise",
        "## Scope and versioning",
        "## MCP tools and responses",
        "## Database and migrations",
        "## CLI",
        "## Machine-readable JSON",
        "## Human-readable formats",
        "## What this promise does not include",
    ):
        assert heading in document


def test_compatibility_document_is_linked_from_readme_and_mcp_interface() -> None:
    assert "[docs/compatibility.md](docs/compatibility.md)" in _read("README.md")
    assert "[compatibility.md](compatibility.md)" in _read("docs/mcp-interface.md")


def test_compatibility_document_relative_links_resolve() -> None:
    targets = _MARKDOWN_LINK.findall(_read("docs/compatibility.md"))

    assert targets, "the document is expected to link to the surfaces it describes"
    for target in targets:
        resolved = (COMPATIBILITY.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link: {target}"


def test_promise_states_additive_mcp_forward_only_db_and_compatible_cli_defaults() -> None:
    document = _read("docs/compatibility.md")

    assert "an existing response field is not removed and its meaning is not repurposed" in document
    assert "a new response field is additive" in document
    assert "**forward-only and additive**" in document
    assert "a new flag is additive and defaults to the previous behavior" in document


def test_promise_documents_stable_json_identifiers_that_match_shipped_code() -> None:
    document = _read("docs/compatibility.md")
    export_defaults = ExportDocument.model_fields

    export_format = export_defaults["format"].default
    export_version = export_defaults["version"].default

    assert f"| `{export_format}` | `{export_version}` |" in document
    assert f"| `{SYNC_BUNDLE_FORMAT}` | `{SYNC_BUNDLE_VERSION}` |" in document


def test_promise_leaves_vault_markdown_unfrozen_and_invents_no_deprecation_window() -> None:
    document = _read("docs/compatibility.md")

    human_formats = document.split("## Human-readable formats", 1)[1].split("##", 1)[0]
    assert "not frozen" in human_formats
    assert "vault export Markdown layout" in human_formats

    exclusions = document.split("## What this promise does not include", 1)[1]
    assert "**A deprecation window.**" in exclusions
    assert "does not invent one" in exclusions
