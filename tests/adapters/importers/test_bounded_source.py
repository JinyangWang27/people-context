"""The read budget importers apply when a bounded process boundary supplies one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from people_context.adapters.importers.bounded_source import (
    SOURCE_TOO_LARGE,
    SourceByteBudget,
    read_source_bytes,
    read_source_text,
    refuse_grown_source,
    verify_source_size,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.app.imports import MAX_CLI_SOURCE_BYTES


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


def test_the_budget_is_a_read_rather_than_a_reported_size(tmp_path: Path) -> None:
    """A size an extractor is *told* is not the size it would have to consume."""
    source = tmp_path / "understated"
    source.write_bytes(b"x" * 64)

    with pytest.raises(ImportExtractionError) as refusal:
        verify_source_size(str(source), max_bytes=8)

    assert refusal.value.code == SOURCE_TOO_LARGE
    verify_source_size(str(source), max_bytes=64)
    verify_source_size(str(source), max_bytes=None)


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


def test_bounded_text_refuses_before_decoding_an_oversized_source(tmp_path: Path) -> None:
    source = tmp_path / "over.csv"
    source.write_text("First Name,Last Name\n", encoding="utf-8")

    with pytest.raises(ImportExtractionError) as refusal:
        read_source_text(str(source), encoding="utf-8-sig", max_bytes=4)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_a_streaming_budget_refuses_at_the_first_byte_past_the_limit() -> None:
    budget = SourceByteBudget(10)
    budget.consume(6)
    budget.consume(4)

    with pytest.raises(ImportExtractionError) as refusal:
        budget.consume(1)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_an_absent_streaming_budget_never_refuses() -> None:
    budget = SourceByteBudget(None)

    budget.consume(MAX_CLI_SOURCE_BYTES * 4)


def test_a_source_that_grew_while_it_was_processed_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "growing"
    source.write_bytes(b"x" * 8)
    verify_source_size(str(source), max_bytes=8)

    source.write_bytes(b"x" * 9)

    with pytest.raises(ImportExtractionError) as refusal:
        refuse_grown_source(str(source), max_bytes=8)

    assert refusal.value.code == SOURCE_TOO_LARGE
    refuse_grown_source(str(source), max_bytes=None)
