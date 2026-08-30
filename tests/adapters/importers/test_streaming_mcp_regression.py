"""The released `import_content` path keeps accepting exactly what it accepted before.

M20 bounds the released MCP surface by streaming, never by rejection: a rejection cap there
would refuse sources `import_content` accepts today, which the compatibility promise forbids
within a major version. These tests pin that distinction for the sources M20.1 converts — the
unbudgeted caller passes no parser-work ceiling at all, and every one of them still reaches
staging with the candidates and the skip report it produced before. `mbox`, `email`, and
`whatsapp` are converted in M20.2 and M20.3 and are covered here only by the router-level
equivalence corpus, which proves they are untouched.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.sqlite import (
    SqliteImportStagingStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.app.imports import UNBOUNDED_IMPORT_BUDGET, ImportContent, ImportPipelineError
from people_context.ports.imports import ExtractedImport

from .equivalence_corpus import CORPUS, GOLDEN_PATH, SourceFixture, fixture_inputs
from .test_streaming_equivalence import run_fixture

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
_STREAMED = ("vcard", "ics", "linkedin", "outlook")
_STREAMED_FIXTURES = [fixture for fixture in CORPUS if fixture.source_type in _STREAMED]
_STREAMED_IDS = [fixture.id for fixture in _STREAMED_FIXTURES]


def _reaches_staging(fixture: SourceFixture) -> bool:
    """Whether a fixture exercises the application path rather than policy that predates M20.

    `ImportContent` derives self identity from the store rather than from a caller's argument,
    and it refuses a batch that yielded no candidate at all. Both are released application
    policy, unrelated to how a source is read, so the fixtures that turn on them are pinned by
    the router-level corpus instead of being restated here against a different baseline.
    """
    recorded = _GOLDEN[f"{fixture.id}::path"]
    return not fixture.self_addresses and bool(recorded.get("refused") or recorded["candidates"])


_APP_FIXTURES = [fixture for fixture in _STREAMED_FIXTURES if _reaches_staging(fixture)]
_APP_IDS = [fixture.id for fixture in _APP_FIXTURES]


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _BudgetRecordingRouter:
    """Forward extraction unchanged while recording the budgets the application supplied."""

    def __init__(self) -> None:
        self._inner = ImportExtractorRouter()
        self.budgets: list[tuple[int | None, int | None, int | None]] = []

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
        self.budgets.append((max_source_bytes, max_candidates, max_retained_parse_records))
        return self._inner.extract(
            source_type,
            content=content,
            path=path,
            self_addresses=self_addresses,
            self_names=self_names,
            self_sender=self_sender,
            content_bytes=content_bytes,
            max_source_bytes=max_source_bytes,
            max_candidates=max_candidates,
            max_retained_parse_records=max_retained_parse_records,
        )


def test_the_released_import_budget_carries_no_parser_work_ceiling() -> None:
    """Streaming is what bounds the MCP path; a ceiling there would be a new rejection."""
    assert UNBOUNDED_IMPORT_BUDGET.max_retained_parse_records is None


def test_an_unbudgeted_caller_reaches_the_extractor_with_no_ceiling_at_all(tmp_path: Path) -> None:
    source = tmp_path / "contacts.vcf"
    source.write_bytes(b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n")
    router = _BudgetRecordingRouter()

    with open_db(":memory:") as conn:
        ImportContent(
            SqlitePeopleRepository(conn),
            router,
            SqliteImportStagingStore(conn),
            _Clock(),
        ).execute("vcard", path=str(source))

    assert router.budgets == [(None, None, None)]


@pytest.mark.parametrize("fixture", _APP_FIXTURES, ids=_APP_IDS)
def test_import_content_stages_the_same_batch_it_staged_before(
    fixture: SourceFixture,
    tmp_path: Path,
) -> None:
    """A source `import_content` accepted before is accepted now, and refusals are unchanged.

    Staging reorders candidates so people commit before their dependants and rewrites their
    batch-local refs, so what is compared here is what survives that: the candidate vocabulary
    the batch carries and the skip report beside it. Extraction order itself is pinned exactly
    by the router-level equivalence corpus.
    """
    expected = _GOLDEN[f"{fixture.id}::path"]
    path = fixture_inputs(fixture, "path", tmp_path)["path"]

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        importer = ImportContent(SqlitePeopleRepository(conn), ImportExtractorRouter(), staging, _Clock())

        if "refused" in expected:
            with pytest.raises((ImportPipelineError, ImportExtractionError)) as refusal:
                importer.execute(fixture.source_type, path=path)
            assert refusal.value.code == expected["refused"]
            return

        batch = importer.execute(fixture.source_type, path=path)
        staged = [row.candidate for row in staging.list_batch(batch.batch_id)]

    assert Counter(candidate["type"] for candidate in staged) == Counter(
        candidate["type"] for candidate in expected["candidates"]
    )
    assert batch.skipped_cards == expected["skipped_cards"]


@pytest.mark.parametrize("fixture", _STREAMED_FIXTURES, ids=_STREAMED_IDS)
def test_no_new_rejection_appears_on_the_unbudgeted_extraction_path(
    fixture: SourceFixture,
    tmp_path: Path,
) -> None:
    """Only the refusals already recorded may occur; streaming introduces none of its own."""
    expected = _GOLDEN[f"{fixture.id}::path"]
    result = run_fixture(fixture, "path", tmp_path)

    assert ("refused" in result) == ("refused" in expected)
    assert result == expected
