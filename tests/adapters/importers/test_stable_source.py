"""Stable-snapshot extraction and extraction-fingerprint tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.stable_source import (
    MAX_STABLE_PASS_ATTEMPTS,
    SOURCE_CHANGED_DURING_IMPORT,
    VerifiedSnapshotExtractor,
)
from people_context.ports.imports import ExtractedImport

_LINKEDIN_HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes\n"
_LINKEDIN_ROW = "Sofia,Rossi,https://example.invalid/in/sr,sofia@example.com,Acme,Engineer,23 Jul 2026,note\n"

_CHAT = "[2026-07-20, 09:00:00] You: morning\n[2026-07-20, 09:01:00] Priya Nair: morning!\n"


def _linkedin(tmp_path: Path, rows: str = _LINKEDIN_ROW) -> Path:
    source = tmp_path / "connections.csv"
    source.write_text(_LINKEDIN_HEADER + rows, encoding="utf-8")
    return source


def _mbox(tmp_path: Path, subject: str = "Hello") -> Path:
    source = tmp_path / "mail.mbox"
    source.write_text(
        "From alice@example.com Mon Jul 20 09:00:00 2026\n"
        "From: Alice <alice@example.com>\n"
        "To: You <you@example.com>\n"
        f"Subject: {subject}\n"
        "Message-ID: <one@example.com>\n"
        "Date: Mon, 20 Jul 2026 09:00:00 +0000\n"
        "\n"
        "body\n",
        encoding="utf-8",
    )
    return source


class _RecordingExtractor:
    """Wrap nothing: report exactly what the stable extractor handed the parser."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def extract(self, source_type: str, **kwargs: Any) -> ExtractedImport:
        self.calls.append(dict(kwargs))
        return self._inner.extract(source_type, **kwargs)


def test_a_byte_capable_source_is_hashed_and_parsed_from_the_same_bytes(tmp_path: Path) -> None:
    source = _linkedin(tmp_path)
    from people_context.adapters.importers.router import ImportExtractorRouter

    recorder = _RecordingExtractor(ImportExtractorRouter())

    stable = VerifiedSnapshotExtractor(recorder).extract_stable(
        "linkedin",
        path=str(source),
        self_addresses=set(),
    )

    assert stable.content_digest == hashlib.sha256(source.read_bytes()).hexdigest()
    # The parser was handed the snapshot, never the path: there is no second read to race.
    assert recorder.calls[0]["path"] is None
    assert recorder.calls[0]["content_bytes"] == source.read_bytes()
    assert [candidate["name"] for candidate in stable.extracted.candidates if candidate["type"] == "person"] == [
        "Sofia Rossi"
    ]


def test_a_byte_snapshot_and_a_path_read_produce_identical_candidates(tmp_path: Path) -> None:
    from people_context.adapters.importers.router import ImportExtractorRouter

    source = _linkedin(tmp_path)
    router = ImportExtractorRouter()

    through_path = router.extract("linkedin", content=None, path=str(source), self_addresses=set())
    through_bytes = VerifiedSnapshotExtractor(router).extract_stable(
        "linkedin",
        path=str(source),
        self_addresses=set(),
    )

    assert through_bytes.extracted.candidates == through_path.candidates


def test_a_utf8_sig_source_decodes_the_same_through_a_snapshot(tmp_path: Path) -> None:
    from people_context.adapters.importers.router import ImportExtractorRouter

    source = tmp_path / "connections.csv"
    source.write_text(_LINKEDIN_HEADER + _LINKEDIN_ROW, encoding="utf-8-sig")
    router = ImportExtractorRouter()

    stable = VerifiedSnapshotExtractor(router).extract_stable("linkedin", path=str(source), self_addresses=set())

    assert [candidate["name"] for candidate in stable.extracted.candidates if candidate["type"] == "person"] == [
        "Sofia Rossi"
    ]


def test_a_single_email_is_hashed_and_parsed_from_the_same_bytes(tmp_path: Path) -> None:
    source = tmp_path / "message.eml"
    source.write_text(
        "From: Alice Ahmed <alice@example.com>\n"
        "To: You <you@example.com>\n"
        "Subject: Weekly sync\n"
        "Message-ID: <one@example.com>\n"
        "Date: Mon, 20 Jul 2026 09:00:00 +0000\n"
        "\n"
        "body\n",
        encoding="utf-8",
    )

    stable = VerifiedSnapshotExtractor().extract_stable(
        "email",
        path=str(source),
        self_addresses={"you@example.com"},
    )

    assert stable.content_digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert [candidate.email for candidate in stable.extracted.people] == ["alice@example.com"]


def test_an_oversized_source_is_refused_before_it_is_hashed(tmp_path: Path) -> None:
    source = _linkedin(tmp_path, _LINKEDIN_ROW * 50)

    with pytest.raises(ImportExtractionError) as excinfo:
        VerifiedSnapshotExtractor().extract_stable(
            "linkedin",
            path=str(source),
            self_addresses=set(),
            max_source_bytes=32,
        )

    assert excinfo.value.code == "source_too_large"


def test_a_path_only_source_is_verified_by_rehashing_after_extraction(tmp_path: Path) -> None:
    source = _mbox(tmp_path)

    stable = VerifiedSnapshotExtractor().extract_stable("mbox", path=str(source), self_addresses=set())

    assert stable.content_digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert stable.extracted.people


def test_a_path_only_source_changed_during_every_pass_stages_nothing(tmp_path: Path) -> None:
    source = _mbox(tmp_path)
    attempts: list[int] = []

    class _RewritingExtractor:
        """Rewrite the source mid-pass, exactly as a mail client appending would."""

        def __init__(self) -> None:
            from people_context.adapters.importers.router import ImportExtractorRouter

            self._inner = ImportExtractorRouter()

        def extract(self, source_type: str, **kwargs: Any) -> ExtractedImport:
            attempts.append(1)
            extracted = self._inner.extract(source_type, **kwargs)
            source.write_text(
                source.read_text(encoding="utf-8") + f"\nX-Attempt: {len(attempts)}\n",
                encoding="utf-8",
            )
            return extracted

    with pytest.raises(ImportExtractionError) as excinfo:
        VerifiedSnapshotExtractor(_RewritingExtractor()).extract_stable(
            "mbox",
            path=str(source),
            self_addresses=set(),
        )

    assert excinfo.value.code == SOURCE_CHANGED_DURING_IMPORT
    assert len(attempts) == MAX_STABLE_PASS_ATTEMPTS
    # The refusal names the limit and the outcome, never a byte of the source.
    assert "X-Attempt" not in str(excinfo.value)


def test_a_path_only_source_that_settles_is_retried_and_accepted(tmp_path: Path) -> None:
    source = _mbox(tmp_path)
    attempts: list[int] = []

    class _SettlingExtractor:
        def __init__(self) -> None:
            from people_context.adapters.importers.router import ImportExtractorRouter

            self._inner = ImportExtractorRouter()

        def extract(self, source_type: str, **kwargs: Any) -> ExtractedImport:
            attempts.append(1)
            extracted = self._inner.extract(source_type, **kwargs)
            if len(attempts) == 1:
                source.write_text(source.read_text(encoding="utf-8") + "X-Late: 1\n", encoding="utf-8")
            return extracted

    stable = VerifiedSnapshotExtractor(_SettlingExtractor()).extract_stable(
        "mbox",
        path=str(source),
        self_addresses=set(),
    )

    assert len(attempts) == 2
    assert stable.content_digest == hashlib.sha256(source.read_bytes()).hexdigest()


def test_a_same_size_in_place_rewrite_is_still_detected(tmp_path: Path) -> None:
    """Metadata alone would miss this: only the rehash proves which bytes were parsed."""
    source = _mbox(tmp_path, subject="Hello")
    original_stat = os.stat(source)

    class _SameSizeRewriter:
        def __init__(self) -> None:
            from people_context.adapters.importers.router import ImportExtractorRouter

            self._inner = ImportExtractorRouter()

        def extract(self, source_type: str, **kwargs: Any) -> ExtractedImport:
            extracted = self._inner.extract(source_type, **kwargs)
            # Toggle between two same-length subjects so the source never settles: every pass
            # sees identity, size, and modification time unchanged while the content moved.
            text = source.read_text(encoding="utf-8")
            swapped = (
                text.replace("Subject: Hello", "Subject: HELLO")
                if "Subject: Hello" in text
                else text.replace("Subject: HELLO", "Subject: Hello")
            )
            source.write_text(swapped, encoding="utf-8")
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            return extracted

    with pytest.raises(ImportExtractionError) as excinfo:
        VerifiedSnapshotExtractor(_SameSizeRewriter()).extract_stable(
            "mbox",
            path=str(source),
            self_addresses=set(),
        )

    assert excinfo.value.code == SOURCE_CHANGED_DURING_IMPORT


def test_verification_never_writes_a_copy_of_the_source(tmp_path: Path) -> None:
    source = _mbox(tmp_path)
    before = sorted(entry.name for entry in tmp_path.iterdir())

    VerifiedSnapshotExtractor().extract_stable("mbox", path=str(source), self_addresses=set())

    assert sorted(entry.name for entry in tmp_path.iterdir()) == before


# -- extraction fingerprints -------------------------------------------


def test_the_same_configuration_fingerprints_identically() -> None:
    extractor = VerifiedSnapshotExtractor()

    first = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="You")
    second = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="You")

    assert first.fingerprint == second.fingerprint
    assert first.contract_revision == "whatsapp.1"
    assert len(first.fingerprint) == 64


def test_a_different_chat_self_sender_does_not_alias_to_the_same_claim() -> None:
    extractor = VerifiedSnapshotExtractor()

    mine = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="You")
    theirs = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="Priya Nair")

    assert mine.fingerprint != theirs.fingerprint


def test_equivalently_normalized_chat_configuration_shares_one_claim() -> None:
    """A phone self hint is compared on its digits, so two spellings are one configuration."""
    extractor = VerifiedSnapshotExtractor()

    spaced = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="+1 555 0100")
    bare = extractor.extraction_identity("whatsapp", self_addresses=set(), self_sender="15550100")

    assert spaced.fingerprint == bare.fingerprint


def test_a_source_that_ignores_the_self_sender_keeps_one_claim() -> None:
    """A LinkedIn CSV parses identically either way, so it must stay deduplicable."""
    extractor = VerifiedSnapshotExtractor()

    without = extractor.extraction_identity("linkedin", self_addresses={"me@example.com"})
    with_hint = extractor.extraction_identity(
        "linkedin",
        self_addresses={"me@example.com"},
        self_sender="You",
        self_names={"me"},
    )

    assert without.fingerprint == with_hint.fingerprint


def test_a_changed_self_address_changes_an_address_scoped_claim() -> None:
    extractor = VerifiedSnapshotExtractor()

    first = extractor.extraction_identity("linkedin", self_addresses={"me@example.com"})
    second = extractor.extraction_identity("linkedin", self_addresses={"other@example.com"})

    assert first.fingerprint != second.fingerprint


def test_self_address_order_does_not_change_the_fingerprint() -> None:
    extractor = VerifiedSnapshotExtractor()

    first = extractor.extraction_identity("ics", self_addresses={"a@example.com", "b@example.com"})
    second = extractor.extraction_identity("ics", self_addresses={"b@example.com", "a@example.com"})

    assert first.fingerprint == second.fingerprint


def test_two_sources_with_identical_configuration_do_not_share_a_fingerprint() -> None:
    """The per-source contract revision is part of the identity, so kinds cannot collide."""
    extractor = VerifiedSnapshotExtractor()

    ics = extractor.extraction_identity("ics", self_addresses={"me@example.com"})
    vcard = extractor.extraction_identity("vcard", self_addresses={"me@example.com"})

    assert ics.fingerprint != vcard.fingerprint


def test_an_unsupported_source_type_is_refused() -> None:
    with pytest.raises(ImportExtractionError) as excinfo:
        VerifiedSnapshotExtractor().extraction_identity("telepathy", self_addresses=set())

    assert excinfo.value.code == "invalid_source_type"


def test_a_chat_export_stages_the_expected_participant_through_a_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "chat.txt"
    source.write_text(_CHAT, encoding="utf-8")

    stable = VerifiedSnapshotExtractor().extract_stable(
        "whatsapp",
        path=str(source),
        self_addresses=set(),
        self_sender="You",
    )

    names = sorted(candidate["name"] for candidate in stable.extracted.candidates if candidate["type"] == "person")
    assert names == ["Priya Nair"]
