"""Canonical vCard 3.0/4.0 serializer for the export projection.

The serializer is pure: it takes the projection the use case already filtered and returns
text. Publication is a separate step so the same bytes can go to stdout or, through
`private_file.atomic_write_private_text` in this package, to an owner-only file.

Determinism is the contract. Property order, escaping, folding, and line endings are fixed
here, so the same projection always produces byte-identical output. The folding and escaping
below read like the iCalendar writer's because both formats inherit the same content-line
grammar, but each is specified by its own RFC and the two are free to diverge.
"""

from __future__ import annotations

from people_context.ports.vcard import VCardContact, VCardProjection

# RFC 6350 section 3.2 and RFC 2426 section 2.6: a content line is folded so no line exceeds
# 75 octets, and a continuation begins with one white-space octet that is not part of the value.
_MAX_LINE_OCTETS = 75
_LINE_BREAK = "\r\n"
_CONTINUATION = " "

# RFC 6350 section 3.3 admits no C0 control other than the horizontal tab in a value. CR and LF
# survive this step because the escaping below turns them into the `\n` the format defines.
_CONTROL_CHARACTERS = dict.fromkeys(
    [code for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)] + [0x7F]
)


class CanonicalVCardWriter:
    """Render one filtered projection as deterministic vCard text."""

    def write_vcards(self, projection: VCardProjection) -> str:
        """Return the complete document, one card per contact, in projection order."""
        lines: list[str] = []
        for contact in projection.contacts:
            lines.extend(_render_card(contact, projection.version))
        return "".join(f"{_fold(line)}{_LINE_BREAK}" for line in lines)


def _render_card(contact: VCardContact, version: str) -> list[str]:
    """Render one card in a fixed property order shared by both dialects.

    `N` repeats the whole canonical name in the family-name component and leaves the other
    four empty. The store never recorded a given/family boundary, so splitting on whitespace
    would invent one and hand a consumer a structure that is wrong for most of the world's
    naming conventions.
    """
    lines = [
        "BEGIN:VCARD",
        f"VERSION:{version}",
        f"FN:{_escape(contact.full_name)}",
        f"N:{_escape(contact.full_name)};;;;",
    ]
    if contact.nicknames:
        lines.append("NICKNAME:" + ",".join(_escape(nickname) for nickname in contact.nicknames))
    lines.extend(f"EMAIL:{_escape(email)}" for email in contact.emails)
    if contact.affiliation is not None:
        lines.append(f"ORG:{_escape(contact.affiliation.organization)}")
        lines.append(f"TITLE:{_escape(contact.affiliation.role)}")
    if contact.birthday is not None:
        # Both dialects receive the extended `YYYY-MM-DD` calendar date the store holds.
        # RFC 6350 spells a complete date in the basic `YYYYMMDD` form, but the bundled
        # importer keeps `BDAY` text verbatim, so writing the basic form would reimport a
        # birthday whose value no longer matches the one that was exported. Keeping the
        # stored spelling is what makes the round trip exact; a later importer-normalizing
        # change can revisit the dialect difference on its own.
        lines.append(f"BDAY:{contact.birthday.isoformat()}")
    lines.append("END:VCARD")
    return lines


def _escape(value: str) -> str:
    """Escape one TEXT value per RFC 6350 section 3.4.

    Control characters are dropped first: the write contract does not reject a stored NUL or
    form feed, and copying one through would produce a card a strict consumer can refuse in
    full, losing every other contact with it. The backslash is escaped before the other
    replacements so the escapes introduced afterwards are not doubled.
    """
    return (
        value.translate(_CONTROL_CHARACTERS)
        .replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold one content line to 75 octets without ever splitting a UTF-8 character."""
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        return line
    chunks: list[str] = []
    current = ""
    used = 0
    budget = _MAX_LINE_OCTETS
    for character in line:
        width = len(character.encode("utf-8"))
        if used + width > budget:
            chunks.append(current)
            current = ""
            used = 0
            # A continuation line spends one octet on the leading white space.
            budget = _MAX_LINE_OCTETS - len(_CONTINUATION.encode("utf-8"))
        current += character
        used += width
    chunks.append(current)
    return f"{_LINE_BREAK}{_CONTINUATION}".join(chunks)
