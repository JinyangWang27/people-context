"""SQL fragments shared by the bounded person projections (M19).

The timeline and the consolidation context read the same tables for two different questions, and
both depend on the same three subtleties: ordering stored timestamps by the instant they denote,
naming the import a record came from without multiplying the record, and binding the disclosure
levels a caller may see. Those fragments live here so the two readers cannot drift apart on any of
them — a second copy of the ordering key in particular would be a copy of a bug waiting to happen.

Every function here composes text from module constants and from column names its own module fixes.
Caller input — a person id, a trait id, the disclosure levels — is always a bound parameter.
"""

from __future__ import annotations

from typing import Final

from people_context.domain.shared import Sensitivity

#: A date-only `valid_from` becomes this instant, the same deterministic convention M9.2 fixed for
#: all-day calendar values. A row still carries the date itself, so the granularity is never lost.
DATE_START_OF_DAY: Final = "T00:00:00+00:00"

#: Digits of sub-second precision the ordering key carries — what `datetime.isoformat()` writes.
_FRACTION_DIGITS = 6

#: Characters of a trailing `±HH:MM` offset.
_OFFSET_CHARS = 6


def _local_text(column: str) -> str:
    """Return the stored timestamp with any trailing `±HH:MM` offset removed.

    Only the wall-clock half is wanted, because the fraction is read off it. A value written by
    `datetime.isoformat()` ends in that offset when it is aware and in a digit when it is naive, so
    testing the sixth character from the end distinguishes the two without parsing.
    """
    return (
        f"CASE WHEN substr({column}, -{_OFFSET_CHARS}, 1) IN ('+', '-') "
        f"THEN substr({column}, 1, length({column}) - {_OFFSET_CHARS}) ELSE {column} END"
    )


def sort_key(column: str) -> str:
    """Return the exact UTC ordering key for one stored timestamp.

    Stored timestamps keep whatever offset the writer supplied and some are naive, so no text
    comparison orders them: `2026-06-02T00:00:00+00:00` sorts after `2026-06-01T19:00:00-05:00` in
    text while being the *earlier* instant. The key is therefore built in two halves. Whole seconds
    come from `strftime`, which does the offset conversion — reading a naive value as UTC, the same
    reading the domain helper applies, never in the host timezone — and the stored sub-second digits
    are appended verbatim, zero-padded to six. That second half needs no conversion because every
    real UTC offset is a whole number of minutes: shifting offsets changes the date, hour, and
    minute of a timestamp and never its seconds or its fraction. The result is a lexicographic key
    that is exactly the UTC instant at microsecond precision, so what the database selects is what
    the application's own exact ordering would select.

    `strftime`'s own `%f` is deliberately not used for the fraction: it resolves only to
    milliseconds, which would let a page keep two records from one millisecond and drop the newest.

    `COALESCE` keeps a value SQLite cannot normalize orderable by its own text rather than sinking
    it below every other row, where `LIMIT` would drop it from a page it belongs on. Every timestamp
    this project writes is `datetime.isoformat()` output, which SQLite does normalize, so that
    fallback is a safety net rather than a path the supported writers reach.
    """
    local = _local_text(column)
    fraction = (
        f"CASE WHEN instr({local}, '.') = 0 THEN '{'0' * _FRACTION_DIGITS}' "
        f"ELSE substr(substr({local}, instr({local}, '.') + 1) || '{'0' * _FRACTION_DIGITS}', "
        f"1, {_FRACTION_DIGITS}) END"
    )
    return f"(COALESCE(strftime('%Y-%m-%dT%H:%M:%S', {column}), {column}) || '.' || ({fraction}))"


def source_session(entity_type: str, id_column: str) -> str:
    """Return the subquery naming the earliest import whose candidate committed onto one entity.

    M18.1 allows several candidates to map to one reused entity, so joining the mapping table would
    multiply a record into as many rows as imports touched it. The subquery names the earliest
    mapping instead, with the candidate id breaking an exact tie. "Earliest mapping" is deliberately
    not "created by": a mapping records that a committed candidate *resolved to* this record, and
    `SetRelationship` updates and returns a matching active edge rather than creating a second one,
    so an import that merely reused an edge entered by hand owns a mapping to it too. The field
    therefore says which import first committed a candidate onto this record, which is what the
    mapping table actually knows.

    Both arguments are fixed constants of the calling module — an entry type from the ports
    vocabulary and a column of the branch being composed — never caller input, so the composed text
    carries nothing a caller supplied.
    """
    return (
        "(SELECT m.source_session_id FROM import_candidate_mappings m "
        f"WHERE m.entity_type = '{entity_type}' AND m.entity_id = {id_column} "
        "ORDER BY m.created_at, m.candidate_id LIMIT 1)"
    )


def levels(sensitivities: tuple[Sensitivity, ...]) -> dict[str, object]:
    """Bind the levels a caller may disclose as one named parameter each."""
    return {f"level{index}": level.value for index, level in enumerate(sensitivities)}


def placeholders(bound_levels: dict[str, object]) -> str:
    """Render the bound level names as an `IN` list.

    An empty set becomes `IN (NULL)`, which matches nothing: a caller allowed to disclose no level
    is shown no record carrying one, rather than every record.
    """
    return ", ".join(f":{name}" for name in bound_levels) or "NULL"
