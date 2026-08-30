"""Every source must extract exactly what it extracted before it learned to stream.

`equivalence_golden.json` was recorded from the pre-M20 whole-file implementation and is
regenerated only by a deliberate, documented change to what a source extracts. Comparing
against it covers what review cannot: not just which candidates appear, but their order, their
refs, their skip reasons, and the one-based indexes those reasons carry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.router import ImportExtractorRouter

from .equivalence_corpus import (
    CORPUS,
    GOLDEN_PATH,
    SourceFixture,
    corpus_cases,
    extraction_snapshot,
    fixture_inputs,
)

_GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def run_fixture(fixture: SourceFixture, route: str, tmp_path: Path, **budget: int | None) -> Any:
    """Extract one fixture through one input route, recording a refusal as data rather than raising."""
    try:
        extracted = ImportExtractorRouter().extract(
            fixture.source_type,
            self_addresses=set(fixture.self_addresses),
            self_names=fixture.self_names,
            self_sender=fixture.self_sender,
            **fixture_inputs(fixture, route, tmp_path),
            **budget,
        )
    except ImportExtractionError as refusal:
        return {"refused": refusal.code}
    return extraction_snapshot(extracted)


@pytest.mark.parametrize(
    ("fixture", "route"),
    list(corpus_cases()),
    ids=[f"{fixture.id}-{route}" for fixture, route in corpus_cases()],
)
def test_streaming_extraction_matches_the_recorded_whole_file_result(
    fixture: SourceFixture,
    route: str,
    tmp_path: Path,
) -> None:
    assert run_fixture(fixture, route, tmp_path) == _GOLDEN[f"{fixture.id}::{route}"]


def test_the_corpus_covers_every_supported_source() -> None:
    """Coverage is by construction, so a new source cannot arrive without an equivalence entry."""
    from people_context.adapters.importers.router import SUPPORTED_IMPORT_SOURCES

    assert {fixture.source_type for fixture in CORPUS} == set(SUPPORTED_IMPORT_SOURCES)


def test_the_golden_record_covers_the_corpus_exactly() -> None:
    """A stale golden file must fail loudly rather than silently skip a fixture."""
    assert set(_GOLDEN) == {f"{fixture.id}::{route}" for fixture, route in corpus_cases()}


@pytest.mark.parametrize(
    ("fixture", "route"),
    list(corpus_cases()),
    ids=[f"{fixture.id}-{route}" for fixture, route in corpus_cases()],
)
def test_a_parser_work_backstop_far_above_the_source_changes_nothing(
    fixture: SourceFixture,
    route: str,
    tmp_path: Path,
) -> None:
    """A budget no in-envelope source can reach must be indistinguishable from no budget."""
    bounded = run_fixture(fixture, route, tmp_path, max_retained_parse_records=len(fixture.raw) + 1)

    assert bounded == _GOLDEN[f"{fixture.id}::{route}"]
