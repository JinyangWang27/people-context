"""Canonical serialization and unchanged-importer round trip for vCard export (M14.2)."""

from __future__ import annotations

from datetime import date

from people_context.adapters.filesystem.vcard_writer import CanonicalVCardWriter
from people_context.adapters.importers.vcard import VCardImportExtractor
from people_context.ports.imports import ExtractedImport
from people_context.ports.vcard import (
    VCARD_3_0,
    VCARD_4_0,
    VCardAffiliation,
    VCardContact,
    VCardProjection,
)

_MAX_LINE_OCTETS = 75


def _projection(*contacts: VCardContact, version: str = VCARD_3_0) -> VCardProjection:
    return VCardProjection(version=version, contacts=contacts)


def _render(projection: VCardProjection) -> str:
    return CanonicalVCardWriter().write_vcards(projection)


def _reimport(text: str) -> ExtractedImport:
    """Read the rendered text back through the importer this export must not change."""
    return VCardImportExtractor().extract("vcard", content=text, path=None, self_addresses=set())


def _full_contact() -> VCardContact:
    return VCardContact(
        person_id="01ALICE",
        full_name="Alice Zhang",
        nicknames=("Ali",),
        emails=("alice@example.com",),
        affiliation=VCardAffiliation(organization="Acme", role="Engineer"),
        birthday=date(1985, 4, 12),
    )


def test_renders_canonical_4_0_bytes() -> None:
    """RFC 6350 section 4.3.1 builds a complete date from the ISO 8601 basic format."""
    text = _render(_projection(_full_contact(), version=VCARD_4_0))

    assert text == (
        "BEGIN:VCARD\r\n"
        "VERSION:4.0\r\n"
        "FN:Alice Zhang\r\n"
        "N:Alice Zhang;;;;\r\n"
        "NICKNAME:Ali\r\n"
        "EMAIL:alice@example.com\r\n"
        "ORG:Acme\r\n"
        "TITLE:Engineer\r\n"
        "BDAY:19850412\r\n"
        "END:VCARD\r\n"
    )


def test_renders_canonical_3_0_bytes() -> None:
    """RFC 2425 section 5.8.4 makes the hyphens optional, so the stored spelling stands."""
    text = _render(_projection(_full_contact(), version=VCARD_3_0))

    assert text == (
        "BEGIN:VCARD\r\n"
        "VERSION:3.0\r\n"
        "FN:Alice Zhang\r\n"
        "N:Alice Zhang;;;;\r\n"
        "NICKNAME:Ali\r\n"
        "EMAIL:alice@example.com\r\n"
        "ORG:Acme\r\n"
        "TITLE:Engineer\r\n"
        "BDAY:1985-04-12\r\n"
        "END:VCARD\r\n"
    )


def test_omits_every_property_the_projection_left_empty() -> None:
    text = _render(_projection(VCardContact(person_id="01BOB", full_name="Bob")))

    assert text == "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Bob\r\nN:Bob;;;;\r\nEND:VCARD\r\n"


def test_an_empty_projection_renders_an_empty_document() -> None:
    assert _render(_projection()) == ""


def test_n_is_not_split_on_whitespace() -> None:
    """The store never recorded a given/family boundary, so the writer never guesses one."""
    text = _render(_projection(VCardContact(person_id="01", full_name="Ursula K. Le Guin")))

    assert "N:Ursula K. Le Guin;;;;\r\n" in text
    reimported = _reimport(text)
    # A structured name that matches `FN` adds no alias, so nothing invented survives.
    assert reimported.candidates[0]["aliases"] == []


def test_repeated_renders_are_byte_identical() -> None:
    projection = _projection(_full_contact(), VCardContact(person_id="01BOB", full_name="Bob"))

    assert _render(projection) == _render(projection)


def test_escapes_separators_and_drops_control_characters() -> None:
    contact = VCardContact(
        person_id="01",
        full_name="Nguyễn; Van\\Anh, Jr\x00\x07",
        nicknames=("first, second",),
        affiliation=VCardAffiliation(organization="Acme; Ltd", role="Head, Research"),
    )

    text = _render(_projection(contact))

    assert "FN:Nguyễn\\; Van\\\\Anh\\, Jr\r\n" in text
    assert "NICKNAME:first\\, second\r\n" in text
    assert "ORG:Acme\\; Ltd\r\n" in text
    assert "\x00" not in text and "\x07" not in text


def test_escaped_values_round_trip_through_the_unchanged_importer() -> None:
    contact = VCardContact(
        person_id="01",
        full_name="Ada; Lovelace, the\\first",
        nicknames=("A, L", "Countess"),
        emails=("ada@example.com", "ada.lovelace@example.co.uk"),
        affiliation=VCardAffiliation(organization="Analytical; Engine", role="Head, Research"),
        birthday=date(1815, 12, 10),
    )

    candidates = _reimport(_render(_projection(contact))).candidates

    assert candidates[0]["name"] == "Ada; Lovelace, the\\first"
    assert candidates[0]["aliases"] == [
        {"value": "A, L", "kind": "nickname"},
        {"value": "Countess", "kind": "nickname"},
        {"value": "ada@example.com", "kind": "handle"},
        {"value": "ada.lovelace@example.co.uk", "kind": "handle"},
    ]
    assert candidates[1] == {
        "type": "affiliation",
        "person_ref": "card-1",
        "org": "Analytical; Engine",
        "role": "Head, Research",
    }
    assert candidates[2] == {
        "type": "fact",
        "person_ref": "card-1",
        "predicate": "birthday",
        "value": "1815-12-10",
    }


def test_a_newline_in_a_value_becomes_one_escaped_line() -> None:
    contact = VCardContact(person_id="01", full_name="Two\r\nLines")

    text = _render(_projection(contact))

    assert "FN:Two\\nLines\r\n" in text
    assert _reimport(text).candidates[0]["name"] == "Two\nLines"


def test_long_lines_fold_and_unfold_without_loss() -> None:
    name = "Alexandra " * 12
    text = _render(_projection(VCardContact(person_id="01", full_name=name.strip())))

    assert all(len(line.encode("utf-8")) <= _MAX_LINE_OCTETS for line in text.split("\r\n"))
    assert "\r\n " in text
    assert _reimport(text).candidates[0]["name"] == name.strip()


def test_folding_never_splits_a_multibyte_character() -> None:
    name = "张伟" * 30
    text = _render(_projection(VCardContact(person_id="01", full_name=name)))

    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= _MAX_LINE_OCTETS
    assert _reimport(text).candidates[0]["name"] == name


def test_both_dialects_round_trip_every_field_and_differ_only_in_the_date_spelling() -> None:
    contact = _full_contact()

    four = _reimport(_render(_projection(contact, version=VCARD_4_0))).candidates
    three = _reimport(_render(_projection(contact, version=VCARD_3_0))).candidates

    assert four[:2] == three[:2]
    # The default dialect reimports the stored birthday byte for byte; 4.0 reimports the
    # basic calendar date its own grammar requires.
    assert three[2]["value"] == "1985-04-12"
    assert four[2]["value"] == "19850412"


def test_a_basic_date_keeps_every_component_padded() -> None:
    contact = VCardContact(person_id="01", full_name="Ancient", birthday=date(85, 4, 2))

    assert "BDAY:00850402\r\n" in _render(_projection(contact, version=VCARD_4_0))
    assert "BDAY:0085-04-02\r\n" in _render(_projection(contact, version=VCARD_3_0))


def test_every_rendered_card_is_accepted_by_the_importer() -> None:
    projection = _projection(
        _full_contact(),
        VCardContact(person_id="01BOB", full_name="Bob"),
    )

    reimported = _reimport(_render(projection))

    assert reimported.skipped_cards == []
    assert [candidate["name"] for candidate in reimported.candidates if candidate["type"] == "person"] == [
        "Alice Zhang",
        "Bob",
    ]
