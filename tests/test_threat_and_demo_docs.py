"""Checks that the dated threat comparison and the README demo walkthrough stay true and resolvable."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from people_context import cli

ROOT = Path(__file__).parents[1]
README = ROOT / "README.md"
PRIVACY = ROOT / "docs/privacy-and-safety.md"

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")
#: Absolute Markdown links.
_EXTERNAL_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
#: Heading of the dated comparison; the date is the "as of" note the spec requires.
_COMPARISON_HEADING = re.compile(r"^### Local-first versus cloud-hosted memory, as of (\d{4}-\d{2}-\d{2})$", re.M)

#: Primary vendor documentation hosts. The comparison must not lean on secondary reporting.
_PRIMARY_SOURCE_HOSTS = frozenset(
    {
        "help.openai.com",
        "openai.com",
        "cdn.openai.com",
        "support.anthropic.com",
        "privacy.anthropic.com",
        "docs.mem0.ai",
    }
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _comparison_section() -> str:
    document = _read("docs/privacy-and-safety.md")
    threat_notes = document.split("## Threat model notes", 1)[1]
    match = _COMPARISON_HEADING.search(threat_notes)
    assert match is not None, "the comparison must live under the threat model notes with an 'as of' date"
    return threat_notes[match.start() :]


def _demo_section() -> str:
    return _read("README.md").split("\n## Demo\n", 1)[1].split("\n## ", 1)[0]


def test_comparison_is_a_dated_subsection_of_the_threat_model_notes() -> None:
    threat_notes = _read("docs/privacy-and-safety.md").split("## Threat model notes", 1)[1]
    match = _COMPARISON_HEADING.search(threat_notes)

    assert match is not None
    assert match.group(1) == "2026-08-05", "the recorded review date must match the sourcing pass"


def test_comparison_covers_every_required_axis_for_every_compared_shape() -> None:
    section = _comparison_section()

    for axis in ("Storage at rest", "Vendor breach or legal demand", "Fully offline", "Deletion"):
        assert f"| {axis} |" in section, f"missing comparison axis: {axis}"
    for subject in ("`people-context`", "ChatGPT", "Claude", "Mem0"):
        assert subject in section, f"missing compared subject: {subject}"


def test_comparison_cites_only_primary_vendor_documentation_over_https() -> None:
    targets = _EXTERNAL_LINK.findall(_comparison_section())

    assert targets, "the comparison must cite the vendor documentation it summarizes"
    for target in targets:
        assert target.startswith("https://"), f"insecure source link: {target}"
        host = target.split("/", 3)[2]
        assert host in _PRIMARY_SOURCE_HOSTS, f"not a primary vendor source: {target}"
    for host in ("help.openai.com", "support.anthropic.com", "docs.mem0.ai"):
        assert any(target.split("/", 3)[2] == host for target in targets), f"unsourced vendor: {host}"


def test_comparison_relative_links_resolve() -> None:
    targets = _MARKDOWN_LINK.findall(_comparison_section())

    assert targets, "the comparison must link the local behavior it claims"
    for target in targets:
        resolved = (PRIVACY.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link: {target}"


def test_comparison_states_the_forget_marker_that_the_adapters_actually_write() -> None:
    """The deletion axis is the project's strongest claim, so it must match the shipped redaction payload."""
    marker = json.dumps({"redacted": True})
    section = _comparison_section()

    assert f"`{marker}`" in section
    assert "hard delete" in section
    for module in (
        "src/people_context/adapters/sqlite/forget_store.py",
        "src/people_context/adapters/sqlite/changelog.py",
    ):
        assert '{"redacted": True}' in _read(module), f"{module} no longer writes the documented marker"


def test_comparison_states_the_local_limitations_rather_than_only_advantages() -> None:
    section = _comparison_section()

    assert "What local-first does not buy" in section
    for limitation in ("plaintext SQLite", "no vendor security team", "served on the user directly"):
        assert limitation in section


def test_readme_demo_section_sits_between_why_and_quick_start() -> None:
    readme = _read("README.md")

    why = readme.index("\n## Why\n")
    demo = readme.index("\n## Demo\n")
    quick_start = readme.index("\n## Quick start\n")

    assert why < demo < quick_start


def test_readme_demo_command_names_a_real_console_script() -> None:
    scripts = tomllib.loads(_read("pyproject.toml"))["project"]["scripts"]
    section = _demo_section()

    assert "uvx --from people-context pctx demo --reset" in section
    assert "pctx" in scripts
    assert "people-context-mcp" in scripts


def test_readme_demo_documents_the_isolation_the_command_actually_enforces() -> None:
    section = _demo_section()

    assert "`{XDG_DATA_HOME or ~/.local/share}/people-context/demo.db`" in section
    for ignored in ("`--db`", "`PEOPLE_CONTEXT_DB`", "config", "workspace"):
        assert ignored in section
    assert "-wal`/`-shm`" in section
    assert "never read or modified" in section


def test_readme_demo_walkthrough_matches_the_commands_the_demo_prints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert cli.main(["demo", "--reset"]) == 0
    printed = capsys.readouterr().out
    section = _demo_section()

    demo_path = (tmp_path / "data" / "people-context" / "demo.db").resolve()
    assert f"Demo database: {demo_path}" in printed
    assert "Demo database: /home/you/.local/share/people-context/demo.db" in section

    assert f"people-context-mcp --db {demo_path}" in printed
    assert "Start MCP server: people-context-mcp --db /home/you/.local/share/people-context/demo.db" in section

    for call in ('resolve_person {"query": "Amina Hassan"}', '"depth": 2', "find_connection"):
        assert call in printed, f"the demo no longer prints: {call}"
        assert call in section, f"the README walkthrough omits: {call}"


def test_readme_relative_links_resolve() -> None:
    targets = _MARKDOWN_LINK.findall(_read("README.md"))

    assert targets
    for target in targets:
        resolved = (README.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link: {target}"


def test_readme_links_the_comparison_by_its_generated_anchor() -> None:
    heading = _COMPARISON_HEADING.search(_read("docs/privacy-and-safety.md"))
    assert heading is not None
    anchor = re.sub(r"[^a-z0-9 -]", "", heading.group(0).lstrip("# ").lower()).replace(" ", "-")

    assert f"(docs/privacy-and-safety.md#{anchor})" in _read("README.md")
