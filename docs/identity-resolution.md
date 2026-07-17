# Identity Resolution

This document describes the identity resolution pipeline implemented by `app/resolve_person.py`
(`ResolvePerson`), used by the `resolve_person` MCP tool and, in a broader form, by `search_people`. The goal
is to answer "who does this name refer to, in this user's data" deterministically and explainably, rather
than as an opaque similarity score.

## The 5-stage pipeline

Candidates are gathered by progressively looser matching stages, each of which assigns a score and a
human-readable `match_reason` string:

| Stage | Technique | Score | `match_reason` |
|---|---|---|---|
| 1. Exact match | Exact match against `canonical_name_normalized` or an alias's `value_normalized`, via `PersonReader.find_by_normalized_name` | `1.0` | `"exact"` |
| 2. Normalized match | Unicode NFKC normalization, casefold, diacritic (combining-mark) stripping, whitespace collapse — the same `normalize_name()` used to populate the `_normalized` columns (see [docs/data-model.md](data-model.md)) — catches names that differ only by case, accents, or spacing | `1.0` (folded into stage 1 via the normalized columns) | `"exact"` |
| 3. FTS5 prefix/token match | `PersonReader.search_names`, backed by the `person_search` FTS5 table, prefix (`"tok"*`) and token queries — handles partial names and multi-token queries like "Wang from Acme" | `0.4 + 0.4 * hit.score` (`hit.score` is the adapter's normalized 0–1 FTS relevance) | `"search:<kind>"` where `kind` is `"canonical"` or `"alias"` |
| 4. Fuzzy match (optional, low-confidence tier) | Levenshtein distance ≤ 2 on normalized forms, only consulted when earlier stages are inconclusive | Low, below the exact/search tiers | `"fuzzy"` |
| 5. Hint boosting | Organisation, role, or relationship hints supplied by the caller re-rank (not re-discover) the candidate set produced by stages 1–4 | Additive/multiplicative adjustment to the base score | Reason string is augmented, e.g. `"search:alias+hint:org"` |

`ResolvePerson.execute(query, limit=5)` runs stages 1–3 today (M0): it checks for an exact-normalized match
first (score `1.0`, `"exact"`), then runs `reader.search_names` and maps hits to scores in the
`[0.4, 0.8]` range via `0.4 + 0.4 * hit.score`, so search-tier hits can never be confused with an exact
match. Results are deduplicated by `person_id` (keeping the best score seen), sorted descending, and
candidates below `0.35` are dropped entirely; soft-deleted people are excluded throughout. Stages 4 (fuzzy)
and 5 (hint boosting) are part of the designed pipeline and are planned for **M1** alongside
`get_person_context`'s relevance ranking (see [docs/roadmap.md](roadmap.md)).

## Scoring and explainability

Every candidate returned by `resolve_person` (`ResolutionCandidate`) carries both a `score` (float, `0.0`–
`1.0`) and a `match_reason` string. This is a deliberate design constraint, not an incidental log field: it
means a caller (typically an LLM composing a response, or a human looking at CLI output) can always see
*why* a candidate was suggested — "exact match," "matched via alias search," "matched by fuzzy distance 1,"
"boosted because you mentioned Acme Corp" — rather than trusting an opaque similarity number. This keeps
resolution debuggable and keeps the user able to correct bad matches with confidence about what went wrong.

## The ambiguity contract

`ResolutionResult.ambiguous` is `true` when at least two candidates clear the acceptance threshold and the
gap between the top two scores is small (`< 0.2`). In that case, `resolve_person` returns **all** qualifying
candidates rather than silently picking the top one — the caller is expected to use additional context
(organisation, role, recent conversation, or asking the user directly) to disambiguate. When no candidate
clears the threshold, `candidates` is returned empty, signalling that the caller should offer to create a
new person via `remember_person` rather than force a low-confidence match onto an unrelated existing record.
See [docs/mcp-interface.md](mcp-interface.md#the-ambiguity-contract-of-resolve_person) for how this
surfaces at the tool level.

## Why transliterations are stored aliases, not computed (v1)

The pipeline deliberately does **not** attempt algorithmic transliteration (e.g. automatically deriving
"Wang" from "王" or vice versa) in v1. Instead, transliterations, native-script names, and nicknames are all
stored as `aliases` rows with an explicit `kind` (`native_script`, `transliteration`, `nickname`, …), written
whenever an agent or the user learns a new name variant for a person (via `remember_person` or the planned
`add_alias`). Reasons for this choice:

- Algorithmic transliteration is inherently ambiguous and script/locale-dependent (a single Chinese surname
  can map to multiple romanizations depending on dialect and personal preference; a nickname is not
  derivable from a legal name at all). A wrong automatic guess is worse than no guess.
- Storing the alias once and reusing it is strictly more reliable than recomputing an approximation on every
  query, and it composes naturally with the exact-match stage (stage 1) — once a transliteration is known,
  it resolves with the highest possible confidence, not a fuzzy one.
- It keeps the resolution pipeline itself deterministic and dependency-free; no transliteration library or
  locale data is required in `domain`/`app`.

This may be revisited in a later milestone (e.g. as an optional low-confidence suggestion source feeding
into stage 5's hint boosting), but is explicitly out of scope for v1. See
[docs/data-model.md](data-model.md#aliases) for the `aliases` schema this relies on.
