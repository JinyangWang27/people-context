"""Stable-snapshot extraction: one digest and one candidate set from one source pass.

A path is not a snapshot. Hashing a file and then handing the same path to an extractor leaves a
window in which the file can change, so the receipt would attest to bytes the candidates never
came from. This adapter closes that window in the only two ways available, chosen per source:

1. **Byte-capable sources** are read once into one bounded immutable snapshot. The digest is
   taken over those bytes and the extractor parses those same bytes, so there is no second read
   to race.
2. **Path-only sources** — currently just ``mbox``, whose reader opens the path itself — are
   verified instead: file identity and metadata plus a SHA-256 are captured before extraction and
   again after it, and a pass whose source moved underneath it is discarded and retried a bounded
   number of times before failing safely.

Neither route writes a temporary copy of the user's source. Route 2 pays a second read of the
file rather than duplicating it on disk, because an unencrypted spare copy of personal source
material is a worse cost than reading it twice.

This module also owns the extraction fingerprint, because the fingerprint has to describe what
*these* extractors actually consume. Only the extractor layer knows that a LinkedIn CSV never
looks at a self sender while a WhatsApp export does, and that WhatsApp compares phone identities
on their digits — so a self hint spelled two equivalent ways must not split one source into two
claims. Deriving it anywhere else would be guessing at parsing behaviour from outside.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from people_context.adapters.importers.bounded_source import read_source_bytes, source_too_large
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.normalization import normalize_email
from people_context.adapters.importers.router import ImportExtractorRouter, unsupported_source_type
from people_context.adapters.importers.whatsapp import self_identity_keys
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractionIdentity, ImportExtractor, StableExtraction

#: Stable failure code for a source that kept changing while it was being imported.
SOURCE_CHANGED_DURING_IMPORT: Final = "source_changed_during_import"

#: Sources whose reader owns the file and therefore cannot be handed a byte snapshot.
PATH_ONLY_SOURCES: Final[frozenset[str]] = frozenset({"mbox"})

#: Passes a path-only source gets before its extraction is declared unstable. A source being
#: written to continuously must fail rather than stage an indeterminate mixture, so this is
#: small: it absorbs one unlucky overlap, not a source that is actively growing.
MAX_STABLE_PASS_ATTEMPTS: Final = 3

#: Bytes hashed per read. Verification must not double the memory cost of the read budget, so
#: the digest is streamed rather than taken over one materialized copy of the whole file.
_HASH_CHUNK_BYTES: Final = 1024 * 1024

#: Signature of a source's effective self-identity derivation.
IdentityResolver = Callable[[set[str], "set[str] | None", "str | None"], list[str]]


@dataclass(frozen=True)
class _SourceContract:
    """What one source's extraction identity is made of.

    ``revision`` is bumped when this project intentionally changes that source's parsing
    semantics, so the new behaviour claims a new identity rather than reusing an old batch.
    ``identities`` mirrors the self-identity set the source's extractor actually resolves
    against, so the fingerprint changes exactly when extraction would.
    """

    revision: str
    identities: IdentityResolver


def _by_name(self_addresses: set[str], _self_names: set[str] | None, _self_sender: str | None) -> list[str]:
    """Resolve self addresses the way the header/attendee/card extractors compare them."""
    return sorted({normalized for value in self_addresses if (normalized := normalize_name(value))})


def _by_email(self_addresses: set[str], _self_names: set[str] | None, _self_sender: str | None) -> list[str]:
    """Resolve self addresses the way the CSV contact extractors compare them."""
    return sorted({normalized for value in self_addresses if (normalized := normalize_email(value))})


def _by_chat_identity(
    self_addresses: set[str],
    self_names: set[str] | None,
    self_sender: str | None,
) -> list[str]:
    """Resolve the merged sender identities the chat extractor omits as the user's own."""
    return sorted(self_identity_keys(self_addresses, self_names, self_sender))


#: What People Context's own extraction of each source depends on.
_EXTRACTION_CONTRACTS: Final[dict[str, _SourceContract]] = {
    "email": _SourceContract(revision="email.1", identities=_by_name),
    "mbox": _SourceContract(revision="mbox.1", identities=_by_name),
    "vcard": _SourceContract(revision="vcard.1", identities=_by_name),
    "ics": _SourceContract(revision="ics.1", identities=_by_name),
    "linkedin": _SourceContract(revision="linkedin.1", identities=_by_email),
    "outlook": _SourceContract(revision="outlook.1", identities=_by_email),
    "whatsapp": _SourceContract(revision="whatsapp.1", identities=_by_chat_identity),
}


class VerifiedSnapshotExtractor:
    """Extract a local source under a verified stable-snapshot guarantee."""

    def __init__(self, extractor: ImportExtractor | None = None) -> None:
        self._extractor = extractor or ImportExtractorRouter()

    def extraction_identity(
        self,
        source_type: str,
        *,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
    ) -> ExtractionIdentity:
        """Return the deterministic extraction-configuration identity for one import.

        The canonical form is sorted and separator-fixed, so the same effective configuration
        always fingerprints identically regardless of set iteration order. Only the derived
        digest is ever persisted; the identities below stay in memory.
        """
        contract = self._contract(source_type)
        payload: dict[str, Any] = {
            "revision": contract.revision,
            "identities": contract.identities(self_addresses, self_names, self_sender),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return ExtractionIdentity(
            contract_revision=contract.revision,
            fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def extract_stable(
        self,
        source_type: str,
        *,
        path: str,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
    ) -> StableExtraction:
        """Return the digest and candidates of one verified stable pass over ``path``."""
        self._contract(source_type)
        if source_type in PATH_ONLY_SOURCES:
            return self._verified_path_pass(
                source_type,
                path=path,
                self_addresses=self_addresses,
                self_names=self_names,
                self_sender=self_sender,
                max_source_bytes=max_source_bytes,
                max_candidates=max_candidates,
            )
        raw = read_source_bytes(path, max_bytes=max_source_bytes)
        extracted = self._extractor.extract(
            source_type,
            content=None,
            path=None,
            self_addresses=self_addresses,
            self_names=self_names,
            self_sender=self_sender,
            content_bytes=raw,
            max_source_bytes=max_source_bytes,
            max_candidates=max_candidates,
        )
        return StableExtraction(content_digest=hashlib.sha256(raw).hexdigest(), extracted=extracted)

    @staticmethod
    def _contract(source_type: str) -> _SourceContract:
        """Return the source's contract, refusing exactly as the router refuses.

        Sharing the router's refusal keeps one answer to "which sources exist": a caller that
        names an unsupported one gets the same code and the same list of accepted values whether
        the mistake is caught here or one layer down.
        """
        contract = _EXTRACTION_CONTRACTS.get(source_type)
        if contract is None:
            raise unsupported_source_type()
        return contract

    def _verified_path_pass(
        self,
        source_type: str,
        *,
        path: str,
        self_addresses: set[str],
        self_names: set[str] | None,
        self_sender: str | None,
        max_source_bytes: int | None,
        max_candidates: int | None,
    ) -> StableExtraction:
        """Extract from the path itself, keeping only a pass whose source never moved.

        Rehashing afterwards is mandatory rather than an optimization over the metadata check:
        a same-size in-place rewrite leaves identity and length untouched, so metadata alone
        would happily attest to bytes the candidates did not come from.
        """
        for _attempt in range(MAX_STABLE_PASS_ATTEMPTS):
            before_identity = _file_identity(path)
            before_digest = _digest_file(path, max_bytes=max_source_bytes)
            extracted = self._extractor.extract(
                source_type,
                content=None,
                path=path,
                self_addresses=self_addresses,
                self_names=self_names,
                self_sender=self_sender,
                content_bytes=None,
                max_source_bytes=max_source_bytes,
                max_candidates=max_candidates,
            )
            after_digest = _digest_file(path, max_bytes=max_source_bytes)
            if before_identity == _file_identity(path) and before_digest == after_digest:
                return StableExtraction(content_digest=after_digest, extracted=extracted)
        raise ImportExtractionError(
            SOURCE_CHANGED_DURING_IMPORT,
            "source changed while it was being imported; "
            f"nothing was staged after {MAX_STABLE_PASS_ATTEMPTS} attempts",
        )


def _file_identity(path: str) -> tuple[int, int, int, int]:
    """Return the identity and metadata a same-path source must keep across one extraction.

    Inode and device catch a replaced file whose contents happen to hash the same; size and
    high-resolution modification time catch the ordinary in-place edit cheaply.
    """
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _digest_file(path: str, *, max_bytes: int | None) -> str:
    """Return the file's SHA-256, refusing past the caller's read budget.

    Streaming keeps verification inside the same budget the extraction itself honours: the
    guarantee is about which bytes were parsed, and paying for it must not mean holding a
    second whole copy of an oversized source in memory.
    """
    hasher = hashlib.sha256()
    consumed = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            consumed += len(chunk)
            if max_bytes is not None and consumed > max_bytes:
                raise source_too_large(max_bytes)
            hasher.update(chunk)
    return hasher.hexdigest()
