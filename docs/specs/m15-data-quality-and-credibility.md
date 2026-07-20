# M15 — Data quality, insight, and credibility

Status: Planned. See [docs/roadmap.md](../roadmap.md#m15--data-quality-insight-and-credibility).

## Motivation

After M9/M14 widen imports, long-lived stores need deterministic ways to find duplicates, contradictory facts, and
soft-deleted references. The project also needs a local aggregate inventory, visible bilingual-match explanations,
and reproducible evidence/use-case documentation. Existing repair tools remain explicit and human-approved; M15
finds and explains problems but never auto-fixes them.

## Scope

In scope:

- CLI-only `people-context doctor` findings;
- CLI-only `people-context stats` aggregate inventory;
- additive transliteration-aware resolution detail with unchanged ranking;
- a fictional-data evaluation harness and use-case gallery.

Non-goals:

- auto-applied repair or LLM-based adjudication;
- hosted telemetry/benchmarking;
- new MCP tools for doctor/stats;
- fuzzy cross-script ranking changes.

## Design

### `people-context doctor`

Add `ports/curation.py::CurationReader`, `adapters/sqlite/curation_reader.py`, and `app/run_doctor.py`. SQL finds
candidate evidence; app policy assigns stable codes, ordering, messages, and suggested actions.

Finding classes:

- `duplicate_handle`: two active people share a normalized `handle`; this takes precedence for the pair;
- `duplicate_alias`: two active people share canonical/non-handle normalized name material, excluding pairs already
  reported as duplicate handles;
- `contradictory_fact`: same person and predicate, different values, and periods overlapping under the existing
  `ValidityPeriod.overlaps()` semantics (inclusive endpoints; missing bounds unbounded);
- `dangling_reference`: relationships, affiliations, or interaction participants point at soft-deleted people.

Every finding includes stable ids/evidence and a **structured** suggested action, not an interpolated shell string:

- CLI action: `{ "surface": "cli", "argv": ["people-context", "show", "<person-id>"] }` or delete equivalent;
- MCP operator action: `{ "surface": "mcp", "tool": "merge_people"|"correct_record",
  "arguments": { ...ids... } }`.

Use ids in actions, never names. Human output may render a copyable representation while JSON preserves the
structured form. Findings do not execute anything. Exit code 0 means the report completed, even when findings
exist; non-zero is reserved for errors.

`doctor --json` is a versioned stable machine interface from its first release and follows the M12 additive-field
rule. `--only CODE[,CODE...]` filters after validation of known codes.

### `people-context stats`

Add aggregate-only `ports/stats.py::StatsReader`, its SQLite adapter, and `app/compute_stats.py`. The adapter returns
counts/distributions only, never record text, device display names, or absolute paths:

- entity counts for documented tables;
- alias-kind distribution;
- facts/observations by sensitivity;
- relationship-category distribution;
- audit operations;
- changelog entry counts keyed by opaque device id;
- database storage bytes.

Storage bytes are the sum of the main database file and existing `-wal`/`-shm` companions, because WAL mode can
otherwise make the reported footprint materially incomplete. The adapter may receive/derive the path internally
but returns only byte counts. For `:memory:` or an unavailable filesystem path, return an explicit
`storage_kind`/`database_bytes: null` state rather than a misleading zero.

The CLI reads `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` and `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT` from its own process and
passes explicit booleans to the use case, reported as “in this environment”. It passes the resolved database path
separately for optional display; the app redacts it by default and includes it only with `--include-path`.
Neither app nor adapter starts/probes the MCP server.

`stats --json` is also versioned and stable/additive from day one.

### Transliteration-aware resolution explanations

Preserve existing score, ordering, ambiguity, and `match_reason="exact"`. Add optional descriptive
`match_detail: str | None` for exact normalized matches:

- `canonical_name` when the canonical name matches;
- otherwise `alias:<kind>` such as `alias:native_script` or `alias:transliteration`;
- search/fuzzy paths may leave it null.

If multiple stored values normalize to the query, canonical wins; otherwise use a documented stable alias-kind and
id ordering. The field is descriptive, not a closed enum, so consumers tolerate future values.

### Evaluation and use-case gallery

Add root `evals/` with fictional fixtures, fixed tasks/rubrics, recorded prompts/model ids, and two runs per task
(with/without the MCP server). Publish dated results and harness version in `docs/evals.md`; add three to five
narrative recipes under `docs/use-cases/`. API keys remain environment-only. The harness never points at or copies a
real personal database and is excluded from shipped package artifacts.

## Migration needs

No schema change is required. If `EXPLAIN QUERY PLAN` proves a finding query needs an index, use the next free
additive migration number at implementation time.

## CLI / MCP surface changes

```text
uv run people-context doctor [--json] [--only CODE[,CODE...]]
uv run people-context stats [--json] [--include-path]
```

No doctor/stats MCP tools. `resolve_person` gains only optional `match_detail`.

## Security and privacy

- Doctor juxtaposes personal evidence, but suggested actions are structured ids/argv/tool arguments and never
  shell-interpolated names.
- Doctor/stats JSON is local output outside model disclosure controls; documentation warns users to inspect before
  sharing.
- Stats returns aggregates only and redacts the path by default; aggregate metadata can still be sensitive.
- Gate status comes only from the local CLI process environment.
- Eval fixtures are fictional and keys stay outside source control.
- Alias-kind detail does not reveal an alias value not already implicated by the match.

## Testing strategy

- SQLite curation fixtures cover each finding and clean non-findings; handle precedence prevents duplicate reports.
- Contradictory-period tests cover touching inclusive endpoints and both unbounded sides via
  `ValidityPeriod.overlaps()` parity.
- App tests pin deterministic ordering, stable codes, `--only`, structured CLI/MCP suggested actions, and ids rather
  than names in executable fields.
- Doctor/stats JSON schema-version fixtures enforce additive shape stability.
- Stats adapter tests verify every aggregate, opaque device grouping, main+WAL+SHM size, and explicit in-memory
  state; app/CLI tests cover gate booleans and path redaction.
- Resolution fixtures cover CJK/romanization and a non-CJK pair in both directions, unchanged ranking/reason, and
  deterministic `match_detail`.
- Eval dry run uses a stub agent with no network and validates scoring plumbing.
- `uv run ruff check .` and `uv run pytest -q` fully green.

## Open questions

1. Should an explicitly interactive repair mode be designed only after the report format has real usage?
2. Should future fuzzy cross-script matching remain a separate experimental capability?
3. What is the minimum credible eval task set beyond identity disambiguation and guided drafting?
4. Should headline eval numbers stay only in dated docs rather than the README?
