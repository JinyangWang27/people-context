"""Safe deterministic filesystem writer for an Obsidian relationship vault."""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

from people_context.domain.vault import (
    VaultAffiliation,
    VaultFact,
    VaultOrganization,
    VaultOrganizationMember,
    VaultPerson,
    VaultSnapshot,
)
from people_context.ports.vault import VaultSafetyError

MARKER_FILE = ".people-context-vault"
_MARKER_CONTENT = "people-context vault v1\n"
# Cross-platform illegal path characters plus the characters Obsidian forbids in
# note names because they would break [[wikilink]] targets: [ ] # ^ |.
_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\[\]#^]')
# Windows reserved device names are invalid as file stems regardless of extension.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{digit}" for digit in range(1, 10)), *(f"LPT{digit}" for digit in range(1, 10))}
)


class FileSystemVaultWriter:
    """Regenerate only exporter-owned paths inside an empty or marker-owned directory."""

    def write_vault(self, output: Path, snapshot: VaultSnapshot) -> list[Path]:
        self._prepare_output(output)
        people_dir = output / "People"
        organizations_dir = output / "Organizations"
        people_dir.mkdir()
        organizations_dir.mkdir()
        (output / MARKER_FILE).write_text(_MARKER_CONTENT, encoding="utf-8", newline="\n")

        person_stems = _unique_stems([(person.id, person.name) for person in snapshot.people])
        organization_stems = _unique_stems(
            [(organization.id, organization.name) for organization in snapshot.organizations]
        )
        files = [output / MARKER_FILE]
        for person in snapshot.people:
            path = people_dir / f"{person_stems[person.id]}.md"
            path.write_text(
                _render_person(person, person_stems, organization_stems),
                encoding="utf-8",
                newline="\n",
            )
            files.append(path)
        for organization in snapshot.organizations:
            path = organizations_dir / f"{organization_stems[organization.id]}.md"
            path.write_text(
                _render_organization(organization, person_stems),
                encoding="utf-8",
                newline="\n",
            )
            files.append(path)
        return sorted(files)

    @staticmethod
    def _prepare_output(output: Path) -> None:
        if output.is_symlink():
            raise VaultSafetyError(f"refusing symlink output directory: {output}")
        if output.exists() and not output.is_dir():
            raise VaultSafetyError(f"output is not a directory: {output}")
        if not output.exists():
            output.mkdir(parents=True)
            return
        children = list(output.iterdir())
        marker = output / MARKER_FILE
        if children and (not marker.is_file() or marker.is_symlink()):
            raise VaultSafetyError(f"refusing non-empty unmarked directory: {output}; expected {MARKER_FILE}")
        if not marker.is_file():
            return
        for generated in (marker, output / "People", output / "Organizations"):
            if generated.is_symlink() or generated.is_file():
                generated.unlink()
            elif generated.is_dir():
                shutil.rmtree(generated)


def sanitize_filename(value: str) -> str:
    """Preserve Unicode names while replacing illegal path and wikilink-breaking characters."""
    normalized = unicodedata.normalize("NFC", value)
    sanitized = _ILLEGAL_FILENAME.sub("_", normalized).strip()
    sanitized = sanitized.lstrip(".").rstrip(" .")
    if sanitized.upper() in _WINDOWS_RESERVED:
        sanitized = f"{sanitized}_"
    return sanitized or "unnamed"


def _unique_stems(items: list[tuple[str, str]]) -> dict[str, str]:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for entity_id, name in items:
        stem = sanitize_filename(name)
        grouped[stem.casefold()].append((entity_id, stem))
    result: dict[str, str] = {}
    used: set[str] = set()
    for key in sorted(grouped):
        group = sorted(grouped[key])
        for entity_id, stem in group:
            candidate = stem if len(group) == 1 else f"{stem} ({entity_id[:6]})"
            unique = candidate
            counter = 2
            while unique.casefold() in used:
                unique = f"{candidate}-{counter}"
                counter += 1
            used.add(unique.casefold())
            result[entity_id] = unique
    return result


def _render_person(
    person: VaultPerson,
    people: dict[str, str],
    organizations: dict[str, str],
) -> str:
    aliases = (
        ["aliases: []"]
        if not person.aliases
        else [
            "aliases:",
            *[f"  - {_yaml_scalar(alias)}" for alias in person.aliases],
        ]
    )
    lines = [
        "---",
        *aliases,
        "tags: [people-context/person]",
        f"people-context-id: {person.id}",
        "---",
        "",
        f"# {_plain(person.name)}",
        "",
        "## Summary",
        "",
        _plain(person.summary) if person.summary else "(none)",
        "",
        "## Relationships",
        "",
    ]
    if person.relationships:
        for relationship in person.relationships:
            target = people.get(relationship.other_person_id, relationship.other_person_id)
            suffix = f" — {_plain(relationship.label)}" if relationship.label else ""
            lines.append(f"- {relationship.display_type}:: [[{target}]]{suffix}")
    else:
        lines.append("(none)")
    lines.extend(["", "## Affiliations", ""])
    if person.affiliations:
        for affiliation in person.affiliations:
            target = organizations.get(affiliation.org_id, sanitize_filename(affiliation.org_name))
            lines.append(f"- role:: [[{target}]] — {_plain(affiliation.role)}{_period_prose(affiliation)}")
    else:
        lines.append("(none)")
    lines.extend(["", "## Facts", ""])
    if person.facts:
        for fact in person.facts:
            lines.append(f"- {_plain(fact.predicate)}: {_plain(fact.value)}{_fact_validity(fact)}")
    else:
        lines.append("(none)")
    lines.extend(["", "## Active reminders", ""])
    if person.reminders:
        for reminder in person.reminders:
            due = f" (due {reminder.due_at.isoformat()})" if reminder.due_at else ""
            lines.append(f"- {_plain(reminder.text)}{due}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _render_organization(organization: VaultOrganization, people: dict[str, str]) -> str:
    lines = [
        "---",
        "aliases: []",
        "tags: [people-context/organization]",
        f"people-context-id: {organization.id}",
        "---",
        "",
        f"# {_plain(organization.name)}",
        "",
    ]
    if organization.kind:
        lines.extend([f"Kind: {_plain(organization.kind)}", ""])
    lines.extend(["## People", ""])
    if organization.members:
        for member in organization.members:
            target = people.get(member.person_id, sanitize_filename(member.person_name))
            lines.append(f"- role:: [[{target}]] — {_plain(member.role)}{_period_prose(member)}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _period_prose(value: VaultAffiliation | VaultOrganizationMember) -> str:
    if value.valid_from and value.valid_to:
        return f"; active {value.valid_from.isoformat()} to {value.valid_to.isoformat()}"
    if value.valid_from:
        return f"; active since {value.valid_from.isoformat()}"
    if value.valid_to:
        return f"; active through {value.valid_to.isoformat()}"
    return ""


def _fact_validity(fact: VaultFact) -> str:
    if fact.valid_from and fact.valid_to:
        return f" (valid {fact.valid_from.isoformat()} to {fact.valid_to.isoformat()})"
    if fact.valid_from:
        return f" (valid since {fact.valid_from.isoformat()})"
    if fact.valid_to:
        return f" (valid through {fact.valid_to.isoformat()})"
    return ""


def _yaml_scalar(value: str) -> str:
    return json.dumps(_plain(value), ensure_ascii=False)


def _plain(value: str | None) -> str:
    return " ".join((value or "").split())
