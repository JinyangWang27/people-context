"""Guidance reaches every client: prompts and resources mirror the packaged skill."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.prompts import GUIDE_URI, SELF_URI, load_guide
from people_context.adapters.mcp.server import build_server

ROOT = Path(__file__).parents[3]


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def test_packaged_guide_is_the_usage_skill_body() -> None:
    """One source of truth: the resource text is the skill minus its frontmatter."""
    skill = (ROOT / "skills" / "people-context-usage" / "SKILL.md").read_text(encoding="utf-8")
    body = re.sub(r"\A---\n.*?\n---\n\n?", "", skill, count=1, flags=re.S)

    assert load_guide() == body


def test_resources_and_prompts_are_listed_and_readable(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "prompts.db")

    async def flow(client: Client) -> dict[str, Any]:
        resources = (await client.list_resources()).resources
        prompts = (await client.list_prompts()).prompts
        guide = await client.read_resource(GUIDE_URI)
        self_before = await client.read_resource(SELF_URI)
        await client.call_tool("remember_person", {"name": "Maya Chen", "is_self": True})
        self_after = await client.read_resource(SELF_URI)
        prep = await client.get_prompt("meeting_prep", {"attendees": "Amina, Daniel"})
        return {
            "resource_uris": sorted(str(item.uri) for item in resources),
            "prompt_names": sorted(item.name for item in prompts),
            "guide": guide.contents[0].text,  # type: ignore[union-attr]
            "self_before": self_before.contents[0].text,  # type: ignore[union-attr]
            "self_after": self_after.contents[0].text,  # type: ignore[union-attr]
            "prep": prep.messages[0].content.text,  # type: ignore[union-attr]
        }

    result = _run(server, flow)

    assert result["resource_uris"] == [GUIDE_URI, SELF_URI]
    assert result["prompt_names"] == ["end_of_session_capture", "maintenance_review", "meeting_prep", "remember", "who"]
    assert "Resolve identity first" in result["guide"]
    assert '"found": false' in result["self_before"]
    assert '"canonical_name": "Maya Chen"' in result["self_after"]
    for tool in ("resolve_person", "get_person_context", "get_communication_guidance", "list_reminders"):
        assert f"`{tool}`" in result["prep"]
    assert "commit_import" not in result["prep"]


def test_remember_prompt_matches_the_capture_rules_it_directs_clients_to_use(tmp_path: Path) -> None:
    """Regression: the prompt once told clients to do what `_validate` refuses, so nothing was recorded."""
    server = build_server(db_path=tmp_path / "prompt-rules.db")

    async def flow(client: Client) -> str:
        prompt = await client.get_prompt("remember", {"statement": "Alice is a patient at Mayo Clinic"})
        return prompt.messages[0].content.text  # type: ignore[union-attr]

    body = _run(server, flow)

    assert "on a `note` only" in body
    assert "refused" in body
    assert "invalid_request" in body


def test_resolve_then_read_prompts_stop_on_a_near_spelling(tmp_path: Path) -> None:
    """The prompts drive resolve-then-id reads, which bypass the wrapper's own fuzzy guard."""
    server = build_server(db_path=tmp_path / "prompt-fuzzy.db")

    async def flow(client: Client) -> dict[str, str]:
        return {
            "who": (await client.get_prompt("who", {"name": "Danial"})).messages[0].content.text,  # type: ignore[union-attr]
            "meeting_prep": (await client.get_prompt("meeting_prep", {"attendees": "Danial"})).messages[0].content.text,  # type: ignore[union-attr]
            "maintenance_review": (await client.get_prompt("maintenance_review", {"name": "Danial"}))
            .messages[0]
            .content.text,  # type: ignore[union-attr]
        }

    prompts = _run(server, flow)

    for name, body in prompts.items():
        assert "fuzzy" in body, name
        assert "ambiguous" in body, name
