"""In-place editing of a staged batch: what an amendment or withdrawal is allowed to do.

Staging already separates what an agent extracted from what the user accepted. Review's two
released verbs, accept and ignore, make that gate lossy: a candidate whose role is stale or whose
date is a year out can only be accepted as written or abandoned. Amendment is the reviewer
finishing the extraction, and withdrawal is saying so out loud instead of silently not accepting.

Neither is a new kind of write. Nothing has been asserted about anybody until commit, so an
amendment writes no audit entry and mints no changelog row, and a withdrawal only journals the
one piece of durable state it can move — the source receipt.

Everything here validates; nothing here writes. The rules are declared once and applied to the
batch *as it would stand after every edit in the request*, because a batch is not a bag of rows:
a reference names another row, and a row's legality can be changed by an edit to its neighbour.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from people_context.app.imports.identity import (
    MatchDisposition,
    candidate_identity_tokens,
    collision_page,
    match_person_candidate,
    person_candidate_matches,
)
from people_context.app.imports.limits import (
    STAGED_PAYLOAD_TOO_LARGE,
    ImportBudget,
    enforce_patch_limits,
    resource_limit_error,
)
from people_context.app.imports.models import (
    CANDIDATE_MODELS,
    MAX_MATCH_CANDIDATE_NAME_CHARS,
    MAX_MATCH_CANDIDATES,
    ImportPipelineError,
)
from people_context.domain.import_provenance import (
    STAGING_STATUS_PENDING,
    staged_candidate_references,
    staged_evidence_references,
    staged_group_references,
)
from people_context.domain.shared import normalize_name
from people_context.domain.staged_candidate import STAGED_CANDIDATE_MODELS, parse_staged_candidate
from people_context.ports.imports import StagedImportRow
from people_context.ports.repository import PersonReader

#: Persisted fields an amendment may never set directly.
#:
#: `type` is excluded because a different type is a different candidate with different dependents;
#: re-stage instead. The two match outcomes are excluded because they are *computed*: they record
#: what the matcher concluded, and a patch that wrote them would be a guess written over an
#: ambiguity. `matched_person_id` is the one exception, and it is not an exception to the rule —
#: a choice is only accepted when the matcher itself produced it. See `_apply_person_match`.
IMMUTABLE_PATCH_FIELDS: frozenset[str] = frozenset({"type", "match_disposition", "match_count"})

#: Patch fields that can change who a person candidate resolves to.
#:
#: Matching re-runs only for these. Every other field is a correction to what the candidate
#: *says*, not to who it is about, and re-deriving the match for one of those would let an
#: unrelated edit discard a resolution the reviewer explicitly made.
IDENTITY_PATCH_FIELDS: frozenset[str] = frozenset({"name", "aliases", "matched_person_id"})

#: What a refusal says instead of a field name the candidate models do not declare.
#:
#: A patch key is untrusted input. An agent or a hand-edited document may put source wording in
#: one, and every refusal here is rendered to a terminal or returned over MCP.
REDACTED_FIELD = "(redacted)"

#: What a refusal says when the rule it broke spans fields rather than naming one.
WHOLE_CANDIDATE = "candidate"

#: The evidence-candidate reference a receiptless batch can never resolve, refused at staging.
_EVIDENCE_REFERENCE_FIELD = "evidence_candidate_ids"


@dataclass(frozen=True)
class BatchEdit:
    """One request to change a batch: patches by candidate id, plus ids to withdraw."""

    amendments: dict[str, dict[str, Any]]
    withdrawals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedEdit:
    """What an accepted edit will write: new candidate bodies, and ids moving to `rejected`."""

    amended: dict[str, dict[str, Any]]
    withdrawn: tuple[str, ...]


def batch_digest(rows: list[StagedImportRow]) -> str:
    """Return a read-time fingerprint of one batch's reviewable content.

    It covers each row's id, status, and stored candidate, in staging order — everything a writer
    can change and a reviewer may have acted on. Read-time projections are deliberately outside
    it: `match_candidates` follows the *person table*, which no batch edit owns, so including it
    would make an unrelated merge elsewhere refuse a reviewer's perfectly current decision.

    Never stored. A stored revision token would make review state durable state; this answers the
    same question — "is this still the batch I was shown?" — and costs nothing between reads.
    """
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                [row.id, row.status, row.candidate],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    return digest.hexdigest()


def require_unchanged_batch(rows: list[StagedImportRow], expected: str | None) -> None:
    """Refuse when the batch moved since the caller was shown it.

    Only when a caller passes a digest. Plain `pctx import commit` and `commit_import` display
    nothing before they act, so they have no view to be stale, and they keep the behaviour they
    always had.
    """
    if expected is None:
        return
    if batch_digest(rows) != expected:
        raise ImportPipelineError(
            "batch_changed",
            "the batch changed since it was read; review it again before acting on it",
            batch_id=rows[0].batch_id if rows else None,
        )


def validate_batch_edit(
    rows: list[StagedImportRow],
    edit: BatchEdit,
    *,
    people: PersonReader,
    tracked: bool,
    budget: ImportBudget,
) -> ValidatedEdit:
    """Return what this edit would write, or refuse it whole without writing anything.

    Order matters and is cheapest-first, but only where that does not change the answer: the
    targets are resolved before any patch is parsed, each patch is bounded before it is merged,
    and the batch-wide rules run last because they are the only ones that need the whole result.
    """
    by_id = {row.id: row for row in rows}
    targets = [*edit.amendments, *edit.withdrawals]
    _require_pending_targets(by_id, targets, batch_id=rows[0].batch_id if rows else None)

    locked = _identity_locked_candidates(rows)
    amended: dict[str, dict[str, Any]] = {}
    for candidate_id, patch in edit.amendments.items():
        amended[candidate_id] = _amended_candidate(
            by_id[candidate_id],
            patch,
            people=people,
            identity_locked=candidate_id in locked,
        )

    resulting = [
        StagedImportRow(
            id=row.id,
            batch_id=row.batch_id,
            source=row.source,
            candidate=amended.get(row.id, row.candidate),
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]
    _check_batch_references(resulting)
    _require_tracking_for_evidence(resulting, amended, tracked=tracked)
    _remeasure_batch(resulting, budget)
    return ValidatedEdit(amended=amended, withdrawn=tuple(edit.withdrawals))


def project_match_candidates(
    people: PersonReader,
    candidate: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Return the colliding people one ambiguous person row could be, and whether more exist.

    Read-time only. The stored row keeps a count, because a projection of identities is a display
    of the person table rather than a second place identity lives.
    """
    tokens = candidate_identity_tokens(str(candidate["name"]), list(candidate.get("aliases", [])))
    # One entry past the cap is all the evidence the truncation flag needs.
    page = collision_page(people, tokens, MAX_MATCH_CANDIDATES + 1)
    truncated = len(page) > MAX_MATCH_CANDIDATES
    projected: list[dict[str, Any]] = []
    for match in page[:MAX_MATCH_CANDIDATES]:
        name = match.canonical_name
        cut = name[:MAX_MATCH_CANDIDATE_NAME_CHARS]
        projected.append({"id": match.id, "canonical_name": cut, "name_truncated": cut != name})
    return projected, truncated


def _identity_locked_candidates(rows: list[StagedImportRow]) -> set[str]:
    """Return the person candidates a committed row in this batch already resolved through.

    Commit resolves a dependent through its person candidate's stored match whether or not that
    person row has itself been committed, so accepting one dependent is enough to pin the
    identity for every other. The person rows those committed dependents name are therefore no
    longer free to move; see `_require_unlocked_identity`.
    """
    people = {row.id for row in rows if row.candidate.get("type") == "person"}
    locked: set[str] = set()
    for row in rows:
        if row.status != "committed":
            continue
        references = (
            staged_candidate_references(row.candidate)
            - staged_evidence_references(row.candidate)
            - staged_group_references(row.candidate)
        )
        locked |= references & people
    return locked


def _require_pending_targets(
    by_id: dict[str, StagedImportRow],
    targets: list[str],
    *,
    batch_id: str | None,
) -> None:
    """Refuse a target this batch does not hold, or one that is no longer open to review.

    Both refusals are whole-request, matching the released selection contract: a caller working
    from a stale list is told so rather than having the part that still parses applied.
    """
    unknown = sorted({target for target in targets if target not in by_id})
    if unknown:
        raise ImportPipelineError(
            "candidate_not_in_batch",
            "candidate does not belong to batch",
            batch_id=batch_id,
            candidate_ids=unknown,
        )
    closed = sorted({target for target in targets if by_id[target].status != STAGING_STATUS_PENDING})
    if closed:
        raise ImportPipelineError(
            "candidate_not_pending",
            "only a pending candidate can be amended or withdrawn",
            batch_id=batch_id,
            candidate_ids=closed,
        )


def _amended_candidate(
    row: StagedImportRow,
    patch: dict[str, Any],
    *,
    people: PersonReader,
    identity_locked: bool = False,
) -> dict[str, Any]:
    """Return one row's candidate with the patch shallow-merged in and re-validated.

    The merge is shallow on purpose. A deep merge makes removing a nested field unexpressible and
    its absence ambiguous — "no aliases key" would be indistinguishable from "keep the aliases you
    had" — so a patched collection replaces the stored one outright.
    """
    if not isinstance(patch, dict):
        raise _amendment_refusal(row, "patch", "a patch is an object of candidate fields")
    enforce_patch_limits(patch)
    kind = str(row.candidate.get("type"))
    for field_name in sorted(IMMUTABLE_PATCH_FIELDS & set(patch)):
        if field_name == "type" and patch["type"] == row.candidate.get("type"):
            continue
        raise _amendment_refusal(row, field_name, _immutable_reason(field_name))
    merged = {**row.candidate, **patch}
    # The shape is checked before the match, not after: matching reads `name` and walks `aliases`,
    # so a patch carrying `{"aliases": null}` or a bare string in that list would raise out of the
    # matcher — a traceback where this boundary promises an atomic refusal.
    _check_persisted_shape(row, merged, kind)
    _apply_person_match(row, merged, patch, people=people, identity_locked=identity_locked)
    _check_input_rules(row, merged, kind)
    return merged


def _immutable_reason(field_name: str) -> str:
    if field_name == "type":
        return "a candidate's type cannot change; stage the material again as the other type"
    return "this field records what identity matching concluded and is not set by a patch"


def _apply_person_match(
    row: StagedImportRow,
    merged: dict[str, Any],
    patch: dict[str, Any],
    *,
    people: PersonReader,
    identity_locked: bool,
) -> None:
    """Re-derive a person candidate's match outcome from the amended values.

    "It is the Priya Sharma at Acme, not the other one" cannot be said through a name and a
    handle: adding a unique handle while keeping the colliding name still unions both people. So a
    patch may name one person directly — and the choice is accepted only when the matcher itself
    found that person, which makes it a reviewer's decision recorded on the row rather than a
    guess written over an ambiguity.

    Without an explicit choice the matcher runs again on the amended tokens, and a candidate that
    stays ambiguous stays ambiguous: its dependents remain unresolved at commit, exactly as they
    are today. Nothing here resolves anything by deciding to.

    It runs again only when the patch actually touches identity. A reviewer who picks one of two
    people named Priya Sharma and then corrects her summary has not reopened the question, and
    re-deriving the match on a name that still collides would quietly put the row back to
    `ambiguous` — discarding the one decision this field exists to record.

    `identity_locked` says a committed row in this batch already resolved through this candidate.
    See `_identity_locked_candidates`.
    """
    if merged.get("type") != "person":
        if "matched_person_id" in patch:
            raise _amendment_refusal(
                row, "matched_person_id", "only a person candidate carries a matched person"
            )
        return
    if not _touches_identity(patch):
        return
    tokens = candidate_identity_tokens(str(merged.get("name", "")), list(merged.get("aliases", [])))
    chosen = patch.get("matched_person_id") if "matched_person_id" in patch else None
    if "matched_person_id" in patch and chosen is not None:
        if not isinstance(chosen, str) or not person_candidate_matches(people, tokens, chosen):
            raise ImportPipelineError(
                "person_not_a_match",
                "the chosen person is not one this candidate's name and handles resolve to",
                candidate_id=row.id,
            )
        _require_unlocked_identity(row, chosen, identity_locked=identity_locked)
        merged["matched_person_id"] = chosen
        merged["match_disposition"] = MatchDisposition.MATCHED.value
        merged["match_count"] = 1
        return
    if row.candidate.get("match_disposition") is None:
        # A row staged before the ambiguity-preserving matcher keeps the first-unique-token
        # behaviour it was staged under, exactly as commit's own re-match does. Re-deriving it
        # with a different matcher would let an amendment change an answer nobody amended.
        legacy = _legacy_match(people, tokens)
        _require_unlocked_identity(row, legacy, identity_locked=identity_locked)
        merged["matched_person_id"] = legacy
        return
    match = match_person_candidate(people, tokens)
    _require_unlocked_identity(row, match.person_id, identity_locked=identity_locked)
    merged["matched_person_id"] = match.person_id
    merged["match_disposition"] = match.disposition.value
    merged["match_count"] = match.match_count


def _touches_identity(patch: dict[str, Any]) -> bool:
    """Whether this patch can change who a person candidate resolves to."""
    return bool(IDENTITY_PATCH_FIELDS & set(patch))


def _require_unlocked_identity(
    row: StagedImportRow,
    resolved: str | None,
    *,
    identity_locked: bool,
) -> None:
    """Refuse an identity change a durable record in this batch has already been written against.

    A pending person row resolves its dependents through its stored match, so a dependent can
    commit while the person row itself is still pending. Letting the identity move afterwards
    would leave that durable record on the old person while the batch says it names the new one,
    and the next dependent would commit to the new one — one batch's records split silently
    across two identities, with nothing in review saying so.

    Only an actual change is refused. Re-affirming the same choice, or a corrected spelling that
    lands on the same person, is not a retarget and stays allowed. The comparison is against the
    *stored* row rather than the merged one, because the merged one already carries the patch.
    """
    if not identity_locked or resolved == row.candidate.get("matched_person_id"):
        return
    raise ImportPipelineError(
        "identity_already_committed",
        "this candidate's identity cannot change: a candidate committed in this batch was "
        "already recorded against the person it resolves to",
        batch_id=row.batch_id,
        candidate_id=row.id,
    )


def _legacy_match(people: PersonReader, tokens: list[str]) -> str | None:
    """Return the first identity token that resolves to exactly one active person, if any."""
    for token in tokens:
        normalized = normalize_name(token)
        if not normalized:
            continue
        matches = people.find_by_normalized_name(normalized)
        if len(matches) == 1:
            return matches[0].id
    return None


def _check_persisted_shape(row: StagedImportRow, merged: dict[str, Any], kind: str) -> None:
    """Hold the amended candidate to the shape staging persists, naming no rejected value."""
    try:
        parse_staged_candidate(merged)
    except ValidationError as exc:
        raise _amendment_refusal(
            row,
            _safe_field(exc.errors()[0].get("loc", ())),
            "the amended candidate is not a valid staged candidate",
        ) from exc


def _check_input_rules(row: StagedImportRow, merged: dict[str, Any], kind: str) -> None:
    """Hold the amended candidate to every input rule staging applied to it.

    The persisted shape is deliberately looser than the staging input, and the gap is not
    cosmetic: a `relationship_type` of `---`, a relationship whose two ends are the same
    candidate, and a fact whose validity starts after it ends all pass the persisted models and
    then raise from inside the commit transaction, after earlier candidates in the same commit
    have already written durable records.

    Rather than restate those rules, the amended candidate is turned back into the request shape
    and re-validated through the model that owns them. That is one copy of the rules instead of
    two, and it covers the cross-field ones — the period checks, the temporal-basis coherence, the
    trait evidence budget — as well as the per-field bounds.

    The batch-local references are stood in for by short placeholders, because their real values
    are checked by `_check_batch_references`, which is the only thing that can see the rest of the
    batch. Distinct references get distinct placeholders, so the rules that care about two
    references being different still see two different things.
    """
    model = CANDIDATE_MODELS.get(kind)
    if model is None:  # pragma: no cover - the persisted shape already refused an unknown type
        raise _amendment_refusal(row, "type", "unsupported candidate type")
    try:
        model.model_validate(_as_request_candidate(merged, kind))
    except ValidationError as exc:
        raise _amendment_refusal(
            row,
            _safe_field(exc.errors()[0].get("loc", ())),
            "the amended candidate is not accepted by the staging boundary",
        ) from exc
    if kind == "relationship" and merged.get("from_candidate_id") == merged.get("to_candidate_id"):
        # The stager owns this one rather than the model, because at request time it is a rule
        # about two batch-local labels. Here it is a rule about two candidate ids, and it matters
        # just as much: an edge from someone to themselves is the self-loop `merge_people` exists
        # to clean up, and commit would leave it unresolved forever rather than write it.
        raise _amendment_refusal(
            row, "to_candidate_id", "a relationship's two ends must be different candidates"
        )


#: How each persisted reference field is spelled in the request the stager validated.
_REFERENCE_FIELD_ALIASES: dict[str, str] = {
    "person_candidate_id": "person_ref",
    "from_candidate_id": "from_ref",
    "to_candidate_id": "to_ref",
    "group_candidate_id": "group_ref",
    "participant_candidate_ids": "participant_refs",
    "evidence_candidate_ids": "evidence_refs",
}

#: The reverse, so a refusal names the field the reviewer can actually see and patch.
_PERSISTED_FIELD_ALIASES: dict[str, str] = {
    request: persisted for persisted, request in _REFERENCE_FIELD_ALIASES.items()
}

#: Persisted-only fields the request shape has no place for.
_COMPUTED_PERSON_FIELDS: tuple[str, ...] = ("matched_person_id", "match_disposition", "match_count")

#: Request-only fields every candidate type declares a batch-local label in.
_SYNTHETIC_REF_TYPES: frozenset[str] = frozenset({"person", "group"})


def _as_request_candidate(merged: dict[str, Any], kind: str) -> dict[str, Any]:
    """Return the amended candidate in the shape the staging request declared it in."""
    placeholders: dict[str, str] = {}

    def placeholder(reference: Any) -> Any:
        if not isinstance(reference, str):
            return reference
        return placeholders.setdefault(reference, f"r{len(placeholders)}")

    request: dict[str, Any] = {}
    for field_name, value in merged.items():
        if field_name in _COMPUTED_PERSON_FIELDS and kind == "person":
            continue
        alias = _REFERENCE_FIELD_ALIASES.get(field_name)
        if alias is None:
            request[field_name] = value
        elif isinstance(value, list):
            request[alias] = [placeholder(item) for item in value]
        else:
            request[alias] = placeholder(value)
    if kind in _SYNTHETIC_REF_TYPES:
        request["ref"] = "r"
    return request


def _check_batch_references(rows: list[StagedImportRow]) -> None:
    """Refuse a batch whose candidate references no longer resolve inside it.

    Shape validation is per row, and a dangling or wrong-type reference passes it: the field is a
    non-blank string either way. Commit resolves the three reference kinds through three different
    maps — people, the evidence candidates a trait cites, the group a membership is placed in — so
    each is checked against the rows it could actually resolve to.

    A withdrawn row is still a legal target. Its pending dependents keep referencing it, and they
    are meant to: they go unresolved at commit rather than becoming unexplainable.
    """
    known = {row.id for row in rows}
    people = {row.id for row in rows if row.candidate.get("type") == "person"}
    evidence = {row.id for row in rows if row.candidate.get("type") in ("observation", "interaction")}
    groups = {row.id for row in rows if row.candidate.get("type") == "group"}
    for row in rows:
        references = staged_candidate_references(row.candidate)
        evidence_references = staged_evidence_references(row.candidate)
        group_references = staged_group_references(row.candidate)
        person_references = references - evidence_references - group_references
        _refuse_references(row, references - known, "names a candidate outside this batch")
        _refuse_references(
            row,
            (person_references & known) - people,
            "names a candidate that is not a person",
        )
        _refuse_references(
            row, (evidence_references & known) - evidence, "cites a candidate that is not evidence"
        )
        _refuse_references(
            row, (group_references & known) - groups, "names a candidate that is not a group"
        )


def _refuse_references(row: StagedImportRow, offending: set[str], reason: str) -> None:
    """Refuse one row's unresolvable references, reporting how many rather than which.

    The count and the row, never the values: a reference may be a restored, format-opaque id, and
    a patch that invented one put untrusted text there.
    """
    if not offending:
        return
    raise ImportPipelineError(
        "candidate_reference_invalid",
        f"an amended candidate {reason}",
        batch_id=row.batch_id,
        candidate_id=row.id,
        invalid_reference_count=len(offending),
    )


def _require_tracking_for_evidence(
    rows: list[StagedImportRow],
    amended: dict[str, dict[str, Any]],
    *,
    tracked: bool,
) -> None:
    """Refuse a same-batch evidence citation this batch could never resolve.

    The rule the stager applies, applied to the same batch later. A batch-local citation resolves
    at commit through the candidate commit mapping, and that mapping exists only for a
    source-tracked batch: without one, a caller who commits the evidence and its trait separately
    strands the trait forever, and nothing would have said so.

    Only an amendment that *adds* a citation is refused. A receiptless batch that somehow already
    holds one is not made worse by an unrelated edit, and refusing every edit to it would leave it
    uncorrectable.
    """
    if tracked:
        return
    for row in rows:
        if row.id not in amended or not row.candidate.get(_EVIDENCE_REFERENCE_FIELD):
            continue
        raise ImportPipelineError(
            "evidence_requires_source_tracking",
            "citing evidence staged in the same batch requires a source-tracked batch; "
            "stage the material again with source_kind, or cite the durable records directly "
            "with evidence_ids",
            batch_id=row.batch_id,
            candidate_id=row.id,
        )


def _remeasure_batch(rows: list[StagedImportRow], budget: ImportBudget) -> None:
    """Refuse an edit that would push the batch past the payload this boundary can read.

    Measured the way the store measures it — staged `source` plus the exact candidate JSON that
    will be written — because an amendment that grew a batch past the review ceiling would make
    `review` and `commit` refuse it, and none of the bounded commands could repair it.
    """
    limit = budget.max_staged_payload_bytes
    if limit is None:
        return
    payload = sum(
        len(row.source.encode("utf-8"))
        + len(json.dumps(row.candidate, ensure_ascii=False).encode("utf-8"))
        for row in rows
    )
    if payload > limit:
        raise resource_limit_error(
            STAGED_PAYLOAD_TOO_LARGE,
            f"the amended batch exceeds the {limit} byte reviewable payload limit for this command",
            batch_id=rows[0].batch_id if rows else None,
            limit=limit,
        )


#: Every field name the persisted candidate models declare, across all of them.
#:
#: The union rather than one type's own fields, matching how the CLI already decides what a
#: validation location may print. A name in this set is this project's own vocabulary and is
#: always safe: `matched_person_id` patched onto an affiliation is a mistake worth naming, not
#: a secret. Anything outside it came from the payload.
_DECLARED_STAGED_FIELDS: frozenset[str] = frozenset(
    field for model in STAGED_CANDIDATE_MODELS.values() for field in model.model_fields
)


def _safe_field(location: tuple[Any, ...]) -> str:
    """Return the field a refusal may name, or `(redacted)` for one the models do not declare.

    A key an editor or an agent invented is untrusted text that may carry source wording. The
    declared field names are this project's own vocabulary and are always safe to print.
    """
    named = False
    for part in location:
        if not isinstance(part, str):
            continue
        named = True
        # A refusal from the request shape names a request field; report the persisted field the
        # reviewer can actually see in `import review` and patch by name. The discriminated union
        # puts its own tag first, so the search continues past a part that names no field rather
        # than reporting the tag as an unprintable key.
        field_name = _PERSISTED_FIELD_ALIASES.get(part, part)
        if field_name in _DECLARED_STAGED_FIELDS:
            return field_name
    if named:
        return REDACTED_FIELD
    # A rule spanning two fields — a reversed validity period, a basis contradicting its dates —
    # names neither of them, and saying `(redacted)` would suggest the patch carried something
    # unprintable when it did not. The candidate as a whole is the honest location.
    return WHOLE_CANDIDATE



def _amendment_refusal(row: StagedImportRow, field_name: str, message: str) -> ImportPipelineError:
    """Return an amendment refusal that names the candidate and the field, never the value."""
    return ImportPipelineError(
        "invalid_candidates",
        message,
        batch_id=row.batch_id,
        candidate_id=row.id,
        details=[{"type": "value_error", "loc": [row.id, field_name], "msg": message}],
        allowed_types=list(CANDIDATE_MODELS),
        valid_fields={name: list(model.model_fields) for name, model in STAGED_CANDIDATE_MODELS.items()},
    )
