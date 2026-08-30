"""Stdlib-only vCard 3.0/4.0 extraction into narrow staged candidates."""

from __future__ import annotations

import quopri
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from people_context.adapters.importers.bounded_source import (
    CandidateBudget,
    ParserWorkBudget,
    iter_split_lines,
    open_source_stream,
)
from people_context.adapters.importers.errors import ImportExtractionError
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
        self_names: set[str] | None = None,
        self_sender: str | None = None,
        content_bytes: bytes | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
        max_retained_parse_records: int | None = None,
    ) -> ExtractedImport:
        """Extract cards; ``self_names`` and ``self_sender`` are unused by this source.

        Cards are unfolded, split, and consumed one at a time, so the only parsed lines held
        live are those of the card currently being read. A file of malformed cards costs the
        skip reasons it reports and nothing more.
        """
        if source_type != "vcard":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'vcard'")
        normalized_self_addresses = {normalize_name(address) for address in self_addresses if address.strip()}
        candidates: list[dict[str, object]] = []
        skipped: list[dict[str, int | str]] = []
        budget = CandidateBudget(max_candidates)
        work = ParserWorkBudget(max_retained_parse_records)
        with open_source_stream(
            content=content,
            content_bytes=content_bytes,
            path=path,
            encoding="utf-8",
            max_bytes=max_source_bytes,
            source_label="vcard",
            universal_newlines=True,
        ) as lines:
            cards = _split_cards(_unfold_lines(iter_split_lines(lines)), work)
            for index, (card_lines, structurally_valid) in enumerate(cards, start=1):
                if not structurally_valid:
                    skipped.append({"index": index, "reason": "malformed_card"})
                    continue
                # Decoding a property can fail long after the file itself decoded cleanly — a
                # `CHARSET` that cannot be resolved, or bytes invalid in the one the card
                # declares. Card independence is the vCard contract, so every decode for this
                # card sits inside one guard and a failure skips only this card.
                try:
                    properties = [_parse_property(line) for line in card_lines]
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
                    card_candidates = _card_candidates(index, name, by_name)
                except (UnicodeDecodeError, ValueError):
                    skipped.append({"index": index, "reason": "malformed_card"})
                    continue
                candidates.extend(card_candidates)
                budget.account(len(candidates))
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


def _unfold_lines(physical: Iterable[str]) -> Iterator[str]:
    """Unfold continuation and quoted-printable soft-break lines as they stream past.

    Only the line still being folded onto is held: a folded line is complete as soon as the
    next physical line turns out not to continue it, which is the point it is yielded.
    """
    pending: str | None = None
    for line in physical:
        if line.startswith((" ", "\t")) and pending is not None:
            pending += line[1:]
        elif pending is not None and pending.endswith("=") and line not in {"BEGIN:VCARD", "END:VCARD"}:
            pending = pending[:-1] + line
        else:
            if pending is not None:
                yield pending
            pending = line
    if pending is not None:
        yield pending


def _split_cards(lines: Iterable[str], work: ParserWorkBudget) -> Iterator[tuple[list[str], bool]]:
    """Yield each card the moment its structural verdict is final.

    A card's verdict cannot be decided before its terminator, so its lines are the one parsed
    record this source retains — and they are accounted as they accumulate, which is what keeps
    a source of many small cards costing one card rather than a file's worth of them.
    """
    current: list[str] | None = None
    malformed = False
    for line in lines:
        marker = line.strip().upper()
        if marker == "BEGIN:VCARD":
            if current is not None:
                yield current, False
            current = []
            malformed = False
        elif marker == "END:VCARD":
            if current is not None:
                yield current, not malformed
                current = None
            else:
                malformed = True
        elif current is not None:
            current.append(line)
            work.account(len(current))
    if current is not None:
        yield current, False


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
        try:
            raw = quopri.decodestring(raw).decode(prop.params.get("CHARSET", "utf-8"))
        except LookupError as exc:
            # The card names a `CHARSET` Python cannot resolve, so this property cannot be
            # read at all. Raised as `ValueError` so it joins the card's existing
            # malformed-card path rather than escaping as an unhandled `LookupError`; the
            # message carries no part of the card, since the charset is source text too.
            raise ValueError("unsupported vcard property charset") from exc
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
