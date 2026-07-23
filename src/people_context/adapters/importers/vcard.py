"""Stdlib-only vCard 3.0/4.0 extraction into narrow staged candidates."""

from __future__ import annotations

import quopri
from dataclasses import dataclass
from pathlib import Path

from people_context.adapters.importers.email import ImportExtractionError
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractedImport

_IGNORED_PROPERTIES = frozenset({"NOTE", "PHOTO", "ADR", "TEL"})
_SUPPORTED_VERSIONS = frozenset({"3.0", "4.0"})


@dataclass(frozen=True)
class _Property:
    name: str
    params: dict[str, str]
    raw_value: str


class VCardImportExtractor:
    """Parse cards independently and retain only identity/affiliation/birthday fields."""

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
    ) -> ExtractedImport:
        if source_type != "vcard":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'vcard'")
        if (content is None) == (path is None):
            raise ImportExtractionError("invalid_source", "vcard import requires exactly one of content or path")
        text = content if content is not None else Path(path or "").read_text(encoding="utf-8")
        cards = _split_cards(_unfold_lines(text))
        normalized_self_addresses = {normalize_name(address) for address in self_addresses if address.strip()}
        candidates: list[dict[str, object]] = []
        skipped: list[dict[str, int | str]] = []
        for index, (lines, structurally_valid) in enumerate(cards, start=1):
            if not structurally_valid:
                skipped.append({"index": index, "reason": "malformed_card"})
                continue
            try:
                properties = [_parse_property(line) for line in lines]
            except (UnicodeDecodeError, ValueError):
                skipped.append({"index": index, "reason": "malformed_card"})
                continue
            by_name: dict[str, list[_Property]] = {}
            for prop in properties:
                by_name.setdefault(prop.name, []).append(prop)
            versions = by_name.get("VERSION", [])
            if len(versions) != 1:
                skipped.append({"index": index, "reason": "malformed_card"})
                continue
            version = _decode_text(versions[0]).strip()
            if version not in _SUPPORTED_VERSIONS:
                skipped.append({"index": index, "reason": "unsupported_version"})
                continue
            fn_properties = by_name.get("FN", [])
            name = _decode_text(fn_properties[0]).strip() if fn_properties else ""
            if not name:
                skipped.append({"index": index, "reason": "missing_fn"})
                continue
            if any(
                normalize_name(_decode_text(email).strip()) in normalized_self_addresses
                for email in by_name.get("EMAIL", [])
            ):
                continue
            candidates.extend(_card_candidates(index, name, by_name))
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=candidates,
            skipped_cards=skipped,
        )


def _card_candidates(index: int, name: str, properties: dict[str, list[_Property]]) -> list[dict[str, object]]:
    ref = f"card-{index}"
    aliases: list[dict[str, str]] = []
    structured = properties.get("N", [])
    if structured:
        parts = _split_escaped(_decode_raw(structured[0]), ";")
        parts.extend([""] * (5 - len(parts)))
        family, given, additional, prefix, suffix = parts[:5]
        structured_name = " ".join(part.strip() for part in (prefix, given, additional, family, suffix) if part.strip())
        if structured_name and normalize_name(structured_name) != normalize_name(name):
            aliases.append({"value": structured_name, "kind": AliasKind.OTHER.value})
    for prop in properties.get("NICKNAME", []):
        for nickname in _split_escaped(_decode_raw(prop), ","):
            value = nickname.strip()
            if value and normalize_name(value) != normalize_name(name):
                aliases.append({"value": value, "kind": AliasKind.NICKNAME.value})
    for prop in properties.get("EMAIL", []):
        value = _decode_text(prop).strip()
        if value:
            aliases.append({"value": value, "kind": AliasKind.HANDLE.value})
    aliases = _dedupe_aliases(aliases)
    candidates: list[dict[str, object]] = [
        {
            "type": "person",
            "ref": ref,
            "name": name,
            "aliases": aliases,
            "message_id": None,
            "date": None,
        }
    ]
    orgs = properties.get("ORG", [])
    titles = properties.get("TITLE", [])
    if orgs and titles:
        org = _split_escaped(_decode_raw(orgs[0]), ";")[0].strip()
        role = _decode_text(titles[0]).strip()
        if org and role:
            candidates.append({"type": "affiliation", "person_ref": ref, "org": org, "role": role})
    birthdays = properties.get("BDAY", [])
    if birthdays:
        birthday = _decode_text(birthdays[0]).strip()
        if birthday:
            candidates.append(
                {
                    "type": "fact",
                    "person_ref": ref,
                    "predicate": "birthday",
                    "value": birthday,
                }
            )
    return candidates


def _unfold_lines(text: str) -> list[str]:
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        elif unfolded and unfolded[-1].endswith("=") and line not in {"BEGIN:VCARD", "END:VCARD"}:
            unfolded[-1] = unfolded[-1][:-1] + line
        else:
            unfolded.append(line)
    return unfolded


def _split_cards(lines: list[str]) -> list[tuple[list[str], bool]]:
    cards: list[tuple[list[str], bool]] = []
    current: list[str] | None = None
    malformed = False
    for line in lines:
        marker = line.strip().upper()
        if marker == "BEGIN:VCARD":
            if current is not None:
                cards.append((current, False))
            current = []
            malformed = False
        elif marker == "END:VCARD":
            if current is not None:
                cards.append((current, not malformed))
                current = None
            else:
                malformed = True
        elif current is not None:
            current.append(line)
    if current is not None:
        cards.append((current, False))
    return cards


def _parse_property(line: str) -> _Property:
    if ":" not in line:
        raise ValueError("malformed vCard property")
    left, raw_value = line.split(":", maxsplit=1)
    pieces = left.split(";")
    name = pieces[0].rsplit(".", maxsplit=1)[-1].upper()
    if not name:
        raise ValueError("malformed vCard property name")
    if name in _IGNORED_PROPERTIES or name.startswith("X-"):
        return _Property(name="IGNORED", params={}, raw_value="")
    params: dict[str, str] = {}
    for item in pieces[1:]:
        if "=" in item:
            key, value = item.split("=", maxsplit=1)
            params[key.upper()] = value
    return _Property(name=name, params=params, raw_value=raw_value)


def _decode_text(prop: _Property) -> str:
    return _unescape_text(_decode_raw(prop))


def _decode_raw(prop: _Property) -> str:
    raw = prop.raw_value
    if prop.params.get("ENCODING", "").casefold() == "quoted-printable":
        raw = quopri.decodestring(raw).decode(prop.params.get("CHARSET", "utf-8"))
    return raw


def _unescape_text(value: str) -> str:
    result: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            result.append("\n" if char in {"n", "N"} else char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)
    if escaped:
        result.append("\\")
    return "".join(result)


def _split_escaped(value: str, separator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == separator:
            parts.append(_unescape_text("".join(current)))
            current = []
        else:
            current.append(char)
    parts.append(_unescape_text("".join(current)))
    return parts


def _dedupe_aliases(aliases: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for alias in aliases:
        key = (normalize_name(alias["value"]), alias["kind"])
        if key not in seen:
            seen.add(key)
            result.append(alias)
    return result
