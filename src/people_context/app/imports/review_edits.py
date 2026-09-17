"""Turn an edited review document back into the batch edits it expresses.

`pctx import edit` hands the operator the `people-context-import-review` document and reads it
back. Only a row's `candidate` object is editable: a candidate that is gone is withdrawn, a
candidate whose fields changed is amended, and an untouched one is a no-op. Every other field the
document renders is a fact about the batch rather than a proposal, so changing one refuses the
whole apply — deleting a row is the only way to withdraw it, and a `status` edited to `rejected`
can never be mistaken for a no-op that a commit prompt then commits.

Nothing here writes, and nothing here echoes a value. An edited file is untrusted input: a refusal
names the row index and a field this document declares, and shows any other key as `(redacted)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Final

from people_context.app.imports.amendment import REDACTED_FIELD
from people_context.app.imports.documents import ImportReviewCandidateEntry, ImportReviewDocument
from people_context.app.imports.models import ImportPipelineError

#: Refusal for an edit to anything the review document renders other than a row's `candidate`.
REVIEW_FIELD_CHANGED: Final = "review_field_changed"

#: Refusal for an edited document that is not the review document's shape at all.
INVALID_REVIEW_DOCUMENT: Final = "invalid_review_document"

#: The deepest nesting level a candidate value reaches in the rendered review document:
#: document → `candidates` → row → `candidate` → `aliases` → alias → alias field. The strict
#: candidate schema fixes this depth; `tests/app/imports/test_review_edits.py` pins it.
RENDERED_MAX_DEPTH: Final = 6

#: Spaces the review renderer indents each nesting level by.
RENDERED_INDENT: Final = 2

#: The smallest value an edit can add to a candidate: a one-character JSON string, `"a"`.
_MIN_ADDED_TOKEN_BYTES: Final = 3

#: The largest ratio of rendered bytes to persisted bytes for anything an edit adds.
#:
#: Staging persists `json.dumps` with its default separators, and the review document is the same
#: JSON indented. Each added element trades its persisted `", "` for a newline and an indent, and a
#: container that stops being empty adds one more before its closing bracket — at most two runs of
#: `1 + indent * depth` bytes per element, which costs at least the smallest token to persist.
RENDERED_EXPANSION: Final = ceil(
    (_MIN_ADDED_TOKEN_BYTES + 2 * (1 + RENDERED_INDENT * RENDERED_MAX_DEPTH)) / _MIN_ADDED_TOKEN_BYTES
)

#: Top-level document fields, and per-row fields, the document declares.
_DOCUMENT_FIELDS: Final = frozenset(ImportReviewDocument.model_fields)
_ROW_FIELDS: Final = frozenset(ImportReviewCandidateEntry.model_fields)

_EDITABLE_ROW_FIELD: Final = "candidate"


@dataclass(frozen=True)
class ReviewEdits:
    """What one edited document asks of its batch: patches by candidate id, and ids to withdraw."""

    amendments: dict[str, dict[str, Any]] = field(default_factory=dict)
    withdrawals: list[str] = field(default_factory=list)


def edited_document_read_bound(rendered_bytes: int, payload_bytes: int, payload_limit: int) -> int:
    """Return how many bytes of edited review document may be read for one batch.

    Derived from the batch rather than from a request bound: the document as rendered now, plus
    the headroom left under the staged-payload ceiling at the renderer's worst-case expansion. An
    unchanged document therefore always fits, and growth the ceiling could never admit is refused
    before it is parsed. Whatever is read is still re-measured against the ceiling when applied.
    """
    return rendered_bytes + max(0, payload_limit - payload_bytes) * RENDERED_EXPANSION


def review_document_edits(rendered: dict[str, Any], edited: object) -> ReviewEdits:
    """Return the edits an edited review document expresses, or refuse the whole document.

    `rendered` is the document as this process rendered it for the same batch. Rows are addressed
    by `id`, falling back to `ordinal`; a row matching neither, or matching a row already claimed,
    is an added row and refuses.
    """
    if not isinstance(edited, dict):
        raise _invalid("the edited review document must be a JSON object")
    _require_same_fields(edited, rendered, _DOCUMENT_FIELDS, location="document")
    for name in ("format", "version", "batch_id"):
        if edited[name] != rendered[name]:
            raise _changed(f"document.{name}")
    if edited["batch_digest"] != rendered["batch_digest"]:
        raise ImportPipelineError(
            "batch_changed",
            "the batch changed since this review document was rendered; review it again before editing",
            batch_id=rendered["batch_id"],
        )
    rows = edited["candidates"]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise _invalid("the edited review document's candidates must be a list of objects")

    by_id = {row["id"]: row for row in rendered["candidates"]}
    by_ordinal = {row["ordinal"]: row for row in rendered["candidates"]}
    claimed: set[str] = set()
    edits = ReviewEdits()
    for index, row in enumerate(rows):
        original = _address(row, by_id, by_ordinal)
        if original is None or original["id"] in claimed:
            raise ImportPipelineError(
                "candidate_not_in_batch",
                f"candidates[{index}] is not a row of the rendered batch; rows cannot be added",
                batch_id=rendered["batch_id"],
                index=index,
            )
        claimed.add(original["id"])
        _require_same_fields(row, original, _ROW_FIELDS, location=f"candidates[{index}]")
        for name in sorted(original.keys() - {_EDITABLE_ROW_FIELD}):
            if row[name] != original[name]:
                raise _changed(f"candidates[{index}].{name}", index=index, field=name)
        candidate = row[_EDITABLE_ROW_FIELD]
        if candidate == original[_EDITABLE_ROW_FIELD]:
            continue
        if not isinstance(candidate, dict):
            raise _invalid(f"candidates[{index}].candidate must be a JSON object", index=index)
        stored = original[_EDITABLE_ROW_FIELD]
        patch = {name: value for name, value in candidate.items() if name not in stored or stored[name] != value}
        # A shallow patch cannot delete a key, so a removed field is sent as null: an optional field
        # is cleared, and a required one is refused by validation under its own declared name.
        patch.update({name: None for name in stored if name not in candidate})
        edits.amendments[original["id"]] = patch
    edits.withdrawals.extend(row["id"] for row in rendered["candidates"] if row["id"] not in claimed)
    return edits


def _address(
    row: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_ordinal: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    identifier = row.get("id")
    if isinstance(identifier, str) and identifier in by_id:
        return by_id[identifier]
    ordinal = row.get("ordinal")
    if isinstance(ordinal, int) and not isinstance(ordinal, bool):
        return by_ordinal.get(ordinal)
    return None


def _require_same_fields(
    edited: dict[str, Any],
    rendered: dict[str, Any],
    declared: frozenset[str],
    *,
    location: str,
) -> None:
    """Refuse an object whose keys differ from the rendered one's, naming only declared keys."""
    difference = sorted(edited.keys() ^ rendered.keys(), key=lambda name: (name not in declared, name))
    if not difference:
        return
    name = difference[0]
    shown = name if name in declared else REDACTED_FIELD
    index = int(location[len("candidates[") : -1]) if location.startswith("candidates[") else None
    raise _changed(f"{location}.{shown}", index=index, field=shown)


def _changed(location: str, *, index: int | None = None, field: str | None = None) -> ImportPipelineError:
    details: dict[str, Any] = {}
    if index is not None:
        details["index"] = index
    if field is not None:
        details["field"] = field
    return ImportPipelineError(
        REVIEW_FIELD_CHANGED,
        f"{location} differs from the rendered review document; only a row's candidate is editable, "
        "and deleting a row withdraws it",
        **details,
    )


def _invalid(message: str, **details: Any) -> ImportPipelineError:
    return ImportPipelineError(INVALID_REVIEW_DOCUMENT, message, **details)
