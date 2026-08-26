"""The read budget importers apply when a bounded process boundary supplies one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from people_context.adapters.importers.bounded_source import (
    SOURCE_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    UNDECODABLE_SOURCE,
    CandidateBudget,
    MeteredSourceFile,
    SourceReadBudget,
    read_source_bytes,
    read_source_text,
    refuse_oversized_file,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.app.imports import MAX_CLI_SOURCE_BYTES, MAX_CLI_STAGED_CANDIDATES


def _sparse_file(path: Path, size: int) -> Path:
    """Create a file of exactly ``size`` zero bytes without spending that much disk."""
    path.touch()
    os.truncate(path, size)
    return path


def test_an_absent_budget_is_the_released_unbounded_read(tmp_path: Path) -> None:
    source = _sparse_file(tmp_path / "big.bin", MAX_CLI_SOURCE_BYTES + 1)

    assert len(read_source_bytes(str(source), max_bytes=None)) == MAX_CLI_SOURCE_BYTES + 1


def test_a_source_exactly_at_the_limit_is_read_rather_than_refused(tmp_path: Path) -> None:
    source = _sparse_file(tmp_path / "exact.bin", MAX_CLI_SOURCE_BYTES)

    assert len(read_source_bytes(str(source), max_bytes=MAX_CLI_SOURCE_BYTES)) == MAX_CLI_SOURCE_BYTES


def test_one_byte_past_the_limit_is_refused_without_naming_the_source(tmp_path: Path) -> None:
    source = _sparse_file(tmp_path / "over.bin", MAX_CLI_SOURCE_BYTES + 1)

    with pytest.raises(ImportExtractionError) as refusal:
        read_source_bytes(str(source), max_bytes=MAX_CLI_SOURCE_BYTES)

    assert refusal.value.code == SOURCE_TOO_LARGE
    assert str(MAX_CLI_SOURCE_BYTES) in str(refusal.value)
    assert str(source) not in str(refusal.value)


def test_an_obviously_oversized_file_is_refused_before_it_is_opened(tmp_path: Path) -> None:
    source = tmp_path / "reported"
    source.write_bytes(b"x" * 64)

    with pytest.raises(ImportExtractionError) as refusal:
        refuse_oversized_file(str(source), max_bytes=8)

    assert refusal.value.code == SOURCE_TOO_LARGE
    refuse_oversized_file(str(source), max_bytes=64)
    refuse_oversized_file(str(source), max_bytes=None)


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        (b"BEGIN\r\nEND\r\n", "utf-8"),
        (b"BEGIN\nEND\n", "utf-8"),
        (b"\xef\xbb\xbfFirst,Last\r\nA,B\r\n", "utf-8-sig"),
        ("naïve — ok\n".encode(), "utf-8"),
    ],
)
def test_bounded_text_decodes_exactly_like_the_unbounded_read(raw: bytes, encoding: str, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(raw)

    bounded = read_source_text(str(source), encoding=encoding, max_bytes=MAX_CLI_SOURCE_BYTES)

    assert bounded == source.read_text(encoding=encoding)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_a_source_in_another_encoding_refuses_instead_of_raising_a_decode_error(
    encoding: str,
    tmp_path: Path,
) -> None:
    """An undecodable file is a source this importer cannot read, not an unhandled crash."""
    source = tmp_path / "latin1.vcf"
    source.write_bytes("BEGIN:VCARD\r\nFN:Café Owner\r\nEND:VCARD\r\n".encode("latin-1"))

    with pytest.raises(ImportExtractionError) as refusal:
        read_source_text(str(source), encoding=encoding, max_bytes=MAX_CLI_SOURCE_BYTES)

    assert refusal.value.code == UNDECODABLE_SOURCE
    assert encoding in str(refusal.value)
    # The offending byte and its offset are still content from an untrusted source.
    assert "0xe9" not in str(refusal.value)
    assert "position" not in str(refusal.value)


def test_an_unbudgeted_read_refuses_an_undecodable_source_the_same_way(tmp_path: Path) -> None:
    source = tmp_path / "latin1.txt"
    source.write_bytes("Café".encode("latin-1"))

    with pytest.raises(ImportExtractionError) as refusal:
        read_source_text(str(source), encoding="utf-8", max_bytes=None)

    assert refusal.value.code == UNDECODABLE_SOURCE


def test_bounded_text_refuses_before_decoding_an_oversized_source(tmp_path: Path) -> None:
    source = tmp_path / "over.csv"
    source.write_text("First Name,Last Name\n", encoding="utf-8")

    with pytest.raises(ImportExtractionError) as refusal:
        read_source_text(str(source), encoding="utf-8-sig", max_bytes=4)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_a_read_budget_refuses_at_the_first_offset_past_the_limit() -> None:
    budget = SourceReadBudget(10)
    budget.observe(10)

    with pytest.raises(ImportExtractionError) as refusal:
        budget.observe(11)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_an_absent_read_budget_never_refuses() -> None:
    SourceReadBudget(None).observe(MAX_CLI_SOURCE_BYTES * 4)


def test_a_metered_file_refuses_once_a_reader_passes_the_budget(tmp_path: Path) -> None:
    source = tmp_path / "scanned"
    source.write_bytes(b"line\n" * 4)

    with source.open("rb") as handle:
        metered = MeteredSourceFile(handle, SourceReadBudget(10))
        assert metered.readline() == b"line\n"
        assert metered.readline() == b"line\n"

        with pytest.raises(ImportExtractionError) as refusal:
            metered.readline()

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_a_metered_file_measures_how_far_it_read_not_how_often(tmp_path: Path) -> None:
    """Re-reading bytes already seen is not more of the file, so it must not refuse."""
    source = tmp_path / "reread"
    source.write_bytes(b"line\n" * 4)

    with source.open("rb") as handle:
        metered = MeteredSourceFile(handle, SourceReadBudget(20))
        assert metered.read() == b"line\n" * 4
        metered.seek(0)

        assert metered.read() == b"line\n" * 4


def test_a_metered_file_delegates_everything_it_does_not_meter(tmp_path: Path) -> None:
    source = tmp_path / "delegated"
    source.write_bytes(b"abc")

    with source.open("rb") as handle:
        metered = MeteredSourceFile(handle, SourceReadBudget(None))

        assert metered.read(1) == b"a"
        assert metered.tell() == 1
        assert metered.seekable() is True
        assert metered.fileno() == handle.fileno()


def test_a_candidate_budget_refuses_past_the_staging_ceiling() -> None:
    budget = CandidateBudget(MAX_CLI_STAGED_CANDIDATES)
    budget.account(MAX_CLI_STAGED_CANDIDATES)

    with pytest.raises(ImportExtractionError) as refusal:
        budget.account(MAX_CLI_STAGED_CANDIDATES + 1)

    assert refusal.value.code == TOO_MANY_CANDIDATES
    assert str(MAX_CLI_STAGED_CANDIDATES) in str(refusal.value)


def test_an_absent_candidate_budget_never_refuses() -> None:
    CandidateBudget(None).account(MAX_CLI_STAGED_CANDIDATES * 100)
