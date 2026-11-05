"""Tests for the opt-in `core` tool set and its classification of every registered tool."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.adapters.mcp.tools import ToolSet
from people_context.adapters.mcp.tools.tool_sets import CORE_TOOLS, EXTENDED_TOOLS, OPERATOR_ELEVATED_TOOLS
from people_context.config import EXPORT_ENV, SENSITIVE_CONTEXT_ENV

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Focused skills shipped in the plugins. Each must work in a `core` session, so every tool one names must be
#: advertised there. The broad `people-context` and `people-context-usage` references cover the full surface.
_FOCUSED_SKILLS = (
    "who",
    "remember",
    "reminders",
    "communication-coach",
    "person-perspective",
    "transcript-review",
)


def _tool_names(server: Any) -> set[str]:
    async def main() -> set[str]:
        async with Client(server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(main)


def _elevate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SENSITIVE_CONTEXT_ENV, "1")
    monkeypatch.setenv(EXPORT_ENV, "1")


def test_every_registered_tool_is_classified_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _elevate(monkeypatch)

    registered = _tool_names(build_server(db_path=tmp_path / "t.db"))

    assert not CORE_TOOLS & EXTENDED_TOOLS
    assert not (CORE_TOOLS | EXTENDED_TOOLS) & OPERATOR_ELEVATED_TOOLS
    assert registered == CORE_TOOLS | EXTENDED_TOOLS | OPERATOR_ELEVATED_TOOLS


def test_default_tool_set_still_advertises_every_ordinary_tool(tmp_path: Path) -> None:
    assert _tool_names(build_server(db_path=tmp_path / "t.db")) == CORE_TOOLS | EXTENDED_TOOLS


def test_core_tool_set_advertises_only_core_tools(tmp_path: Path) -> None:
    assert _tool_names(build_server(db_path=tmp_path / "t.db", tool_set=ToolSet.CORE)) == CORE_TOOLS


def test_core_tool_set_leaves_operator_elevation_in_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _elevate(monkeypatch)

    names = _tool_names(build_server(db_path=tmp_path / "t.db", tool_set=ToolSet.CORE))

    assert names == CORE_TOOLS | OPERATOR_ELEVATED_TOOLS


def test_core_tool_set_does_not_serve_a_tool_it_does_not_advertise(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db", tool_set=ToolSet.CORE)

    async def main() -> Any:
        async with Client(server) as client:
            return await client.call_tool("record_fact", {"person_id": "p", "key": "k", "value": "v"})

    try:
        result = anyio.run(main)
    except Exception as exc:  # noqa: BLE001 - the SDK may raise or return an error result
        assert "record_fact" in str(exc)
    else:
        assert result.is_error is True


@pytest.mark.parametrize("skill", _FOCUSED_SKILLS)
def test_focused_skills_only_name_core_or_elevated_tools(skill: str) -> None:
    text = (_REPO_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    known = CORE_TOOLS | EXTENDED_TOOLS | OPERATOR_ELEVATED_TOOLS
    named = {token for token in re.findall(r"[a-z]+(?:_[a-z]+)+", text) if token in known}

    assert named <= CORE_TOOLS | OPERATOR_ELEVATED_TOOLS


def test_reference_skill_lists_the_same_tool_sets_as_the_server() -> None:
    text = (_REPO_ROOT / "skills" / "people-context" / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## 11. Available tools", 1)[1].split("**Gated**", 1)[0]
    listed: dict[str, set[str]] = {"**Core**": set(), "**Full set only**": set()}
    for paragraph in section.split("\n\n"):
        for label, names in listed.items():
            if paragraph.startswith(label):
                names.update(re.findall(r"`([a-z_]+)`", paragraph))

    assert listed["**Core**"] == CORE_TOOLS
    assert listed["**Full set only**"] == EXTENDED_TOOLS


def _named_tools(text: str) -> set[str]:
    known = CORE_TOOLS | EXTENDED_TOOLS | OPERATOR_ELEVATED_TOOLS
    return {token for token in re.findall(r"[a-z]+(?:_[a-z]+)+", text) if token in known}


def test_a_core_server_names_only_tools_it_lists(tmp_path: Path) -> None:
    """Instructions and prompts are read by the model, so a core server must not direct it to hidden tools."""
    server = build_server(db_path=tmp_path / "t.db", tool_set=ToolSet.CORE)

    async def main() -> tuple[str, list[str]]:
        async with Client(server) as client:
            instructions = client.instructions or ""
            texts = []
            for prompt in (await client.list_prompts()).prompts:
                arguments = {argument.name: "Alice" for argument in prompt.arguments or []}
                result = await client.get_prompt(prompt.name, arguments)
                texts.append(prompt.description or "")
                texts.extend(message.content.text for message in result.messages)  # type: ignore[union-attr]
            return instructions, texts

    instructions, prompt_texts = anyio.run(main)

    assert _named_tools(instructions) <= CORE_TOOLS
    for text in prompt_texts:
        hidden = _named_tools(text) - CORE_TOOLS
        # Naming a hidden tool is allowed only to say it is unavailable in this tool set.
        assert not hidden or "--tools all" in text, hidden


def test_the_full_server_keeps_its_instructions_and_maintenance_prompt(tmp_path: Path) -> None:
    from people_context.adapters.mcp.server import SERVER_INSTRUCTIONS

    server = build_server(db_path=tmp_path / "t.db")

    async def main() -> tuple[str, str]:
        async with Client(server) as client:
            result = await client.get_prompt("maintenance_review", {"name": "Alice"})
            return client.instructions or "", result.messages[0].content.text  # type: ignore[union-attr]

    instructions, maintenance = anyio.run(main)

    assert instructions == SERVER_INSTRUCTIONS
    assert "`find_connection`" in instructions
    assert "`get_consolidation_context`" in maintenance
    assert "`merge_people` only when identity is independently established" in maintenance
