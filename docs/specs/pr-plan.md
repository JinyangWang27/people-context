# M8–M15 pull-request plan

One checklist item below is one pull request. Each PR is scoped to be independently mergeable with CI green,
ordered so it depends only on PRs above it, and sized well under ~1,000 changed lines. An implementing agent
should read the referenced milestone spec in this directory first, then treat the PR's **Spec** bullets as
binding requirements and the **Out of scope** bullets as hard boundaries. Check the box in the same PR that
delivers the work.

Ground rules that apply to every PR (do not restate them per-PR when implementing):

- the hexagonal rule: `domain`/`app` never import adapters or the MCP SDK;
- every durable write path flows through the `audit_mutation` seam in `app/write_support.py`;
- migrations are forward-only additive files under `adapters/sqlite/migrations/` (next free number);
- new `app/` behavior is tested against fake ports and real SQLite; MCP tools get in-memory server tests;
  CLI commands get CLI tests;
- MCP response changes are additive only (binding once the M12 compatibility promise lands);
- `uv run ruff check .` clean and `uv run pytest -q` fully green before every merge.

| Milestone | Theme | PRs |
|---|---|---|
| M8 | Distribution & reach | 4 |
| M9 | Cold start & onboarding | 4 |
| M10 | Agent utilization | 3 |
| M11 | Sync bundle export & bootstrap restore | 4 |
| M12 | Trust, stability, and v1.0 | 4 |
| M13 | Daily utility & proactive signals | 4 |
| M14 | Ecosystem & interoperability | 4 |
| M15 | Data quality, insight, and credibility | 4 |
| **Total** | | **31** |

Cross-milestone dependencies (everything else orders freely by milestone number):

- **M9.1 → M14.3**: the `ImportExtractorRouter` relocation into `adapters/import_router.py` happens once, in
  M9.1; M14.3 builds on it (its "relocate if not already landed" clause exists only for out-of-order work).
- **M9.4 → M12.3**: the README demo walkthrough documents the `people-context demo` command M9.4 ships.
- **M10.1 → M13.3**: the meeting-preparation section extends the skill M10.1 creates.
- **M12.1 → M12.2**: the release checklist references the compatibility promise doc.
- **M14.1 → M14.4**: the Obsidian plugin consumes `list --json` and `brief --json` from M14.1.
- **M13.1/M13.2 and M15.1/M15.2** share conventions (caps/truncation; report-only CLIs) but not code — they
  can proceed in parallel.

## M8 — Distribution & reach

> Cut the distance between "hears about this project" and "has it running in a client" to a single command, without adding runtime dependencies or changing any server behavior.

- [ ] **PR M8.1 — Verify zero-clone `uvx` install and put it first in the README**
  - **Scope:** Verify `uvx --from people-context people-context-mcp --help` and a real stdio round trip work against the published PyPI package on a clean machine; reorder the README Quick start so the `uvx` form leads, ahead of `uv tool install` and the `git clone`/`uv sync` dev path. No code changes — pure verification + docs.
  - **Touches:**
    - `README.md` (Quick start section reorder/rewrite)
    - `docs/releasing.md` (cross-reference note only, if needed)
  - **Spec:** (docs/specs/m8-distribution-and-reach.md, "Zero-clone PyPI install")
    - Primary PyPI distribution is `people-context`; `people-context-mcp` distribution is compatibility-only — examples must reference the primary one.
    - No code change required; `[project.scripts]` already wires `people-context-mcp` and `people-context` entrypoints (`pyproject.toml`).
    - Deliverable is the verification itself (clean-machine `--help` run + one real stdio round trip) plus the README reordering, not new packaging code.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green (no test changes expected, this is docs-only). Manual: run `uvx --from people-context people-context-mcp --help` and one stdio round trip (resolve/remember/context) on a clean environment, record the commands used in the PR description as the acceptance evidence.
  - **Out of scope:** registry/`server.json`, `.mcpb` bundle, editor configs, Docker — all later PRs.

- [ ] **PR M8.2 — MCP Registry `server.json` and `mcp-name` README marker**
  - **Scope:** Add a repository-root `server.json` conforming to the official MCP Registry schema (PyPI package entry, stdio transport) and the required `mcp-name:` ownership marker in the packaged README; add a CI validation step. Does not publish to the Registry (that's a release-flow/manual step) and does not add the `.mcpb` package entry yet.
  - **Touches:**
    - `server.json` (new, repo root)
    - `README.md` (add `mcp-name:` marker, e.g. as an HTML comment near the top so it ships inside the sdist/wheel README)
    - `.github/workflows/ci.yml` or a new step (add `mcp-publisher validate` invocation, run alongside — not folded into — the existing `claude plugin validate . --strict` step referenced from `.github/workflows/claude-plugin-validate.yml`)
    - `docs/releasing.md` (note `mcp-publisher publish` as a release-flow step, if not already automated in this PR)
  - **Spec:** (docs/specs/m8-distribution-and-reach.md, "MCP registry and community directories")
    - `server.json` must use a `packages` entry with `"registryType": "pypi"`, package identifier `people-context`, version, and `"transport": {"type": "stdio"}` — not a raw `command`/`args` invocation.
    - The `mcp-name:` marker must persist through every release since it ships inside the sdist/wheel README.
    - Use the Registry's own `mcp-publisher` tooling (`validate` in CI, `publish` in release flow) — not the Claude plugin validator, which only covers plugin files.
    - Open question (spec section "Open questions" #1): namespace choice (`io.github.jinyangwang27.*` vs custom domain) must be decided and recorded in this PR's description since it's embedded in `server.json`.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New: CI job step running `mcp-publisher validate server.json` (or documented local-equivalent command if the tool isn't sandboxable in CI yet) must pass. No domain/app/adapter code changes, so no new unit tests are required per spec's Testing strategy.
  - **Out of scope:** community-directory metadata files (Smithery/PulseMCP/mcp.so/Glama), `.mcpb` bundle and its `registryType: "mcpb"` package entry, actual `mcp-publisher publish` execution against the live Registry.

- [ ] **PR M8.3 — Claude Desktop `.mcpb` bundle + editor one-line configs**
  - **Scope:** Add a `scripts/build-mcpb.*` packaging script producing a `.mcpb` bundle that invokes `uvx --from people-context people-context-mcp`, wire it into CI as a release-attached artifact, and add README config snippets for Cursor (`.cursor/mcp.json`), Windsurf, and VS Code (`.vscode/mcp.json`), all using the same `uvx` invocation as the canonical form.
  - **Touches:**
    - `scripts/build-mcpb.sh` (or `.py`) (new — first file in a new `scripts/` directory)
    - `.github/workflows/release.yml` (add `.mcpb` build + GitHub Release artifact attach step)
    - `README.md` (MCP client configuration section: add Cursor/Windsurf/VS Code snippets next to the existing Claude Code/generic stdio blocks)
    - `docs/claude-code-plugin.md` (cross-reference only, if the security-model note needs a pointer to the new bundle)
  - **Spec:** (docs/specs/m8-distribution-and-reach.md, "Claude Desktop extension (.mcpb)" and "Editor/IDE one-line configs")
    - `.mcpb` manifest shape is analogous to `.claude-plugin/mcp.json`'s `{"mcpServers": {"people-context": {"type": "stdio", "command": ...}}}` but targets `uvx --from people-context people-context-mcp` so the bundle doesn't vendor a project directory.
    - Exactly one canonical invocation (`uvx --from people-context people-context-mcp`) across every client config — do not add per-client `uv run --directory ...` variants.
    - Every new distribution channel must prominently document that it executes local Python with the launching user's filesystem permissions (docs/privacy-and-safety.md#threat-model-notes), not a sandboxed extension.
    - Resolve open question #2 (whether `.mcpb` can shell out to `uvx` at first run vs. needing a vendored interpreter) and record the decision in the PR description.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New: a CI step that runs the `build-mcpb` script and asserts the artifact is produced (smoke check, not a full Desktop install). Manual: install the `.mcpb` bundle in Claude Desktop and confirm it connects without a local clone; install each editor config on a clean machine and exercise `resolve_person`/`get_person_context`/`remember_person`.
  - **Out of scope:** Docker image and its publish workflow (PR M8.4), the `.mcpb` `server.json` package entry (was optional/could follow here or in M8.2 rework — keep in this PR's follow-up note only if time allows, otherwise defer).

- [ ] **PR M8.4 — Optional Docker image + GHCR publish job**
  - **Scope:** Add a multi-stage `Dockerfile` (built with `uv`, non-root user, stdio-by-default entrypoint) and a new, narrowly-scoped CI workflow that publishes the image to GHCR on tagged releases. Update README/docs with Docker usage documenting the bind-mounted DB volume and `PEOPLE_CONTEXT_DB` env var.
  - **Touches:**
    - `Dockerfile` (new, repo root)
    - `.dockerignore` (new)
    - `.github/workflows/docker-publish.yml` (new — third publish workflow, alongside `release.yml` and `package-publish.yml`, not folded into either)
    - `README.md` (Docker usage section)
  - **Spec:** (docs/specs/m8-distribution-and-reach.md, "Optional Docker image" and Security/privacy considerations)
    - Container runs as non-root, starts `people-context-mcp` over stdio by default.
    - Database path resolution must go through the existing `config.py:resolve_db_path` precedence; documented usage mounts a host directory at the resolved XDG data path and sets `PEOPLE_CONTEXT_DB` explicitly — no container-specific config invented.
    - Must NOT bake in `--http` as a default command (container network namespaces change the "loopback" trust boundary described in docs/privacy-and-safety.md).
    - No outbound network call except the existing, explicit `people-context reindex --semantic` model download path.
    - Any new publish credential (GHCR token) should follow the Trusted Publishing / short-lived, workflow-scoped, least-privilege model already used for PyPI.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New: CI Docker smoke job — builds the image, runs it with a temporary volume, executes `people-context-mcp --help` plus one real stdio round trip (resolve/remember/context), matching the existing `uv run people-context-mcp --help` acceptance pattern from docs/claude-code-plugin.md#local-validation.
  - **Out of scope:** an `--http` variant image (spec open question #3, deferred), community-directory metadata files (can land in a follow-up PR outside this fragment's scope since each directory has an independent, low-risk submission format).

## M9 — Cold start & onboarding

> Give a freshly installed, empty database something to show in under a minute, and broaden the extract-and-stage import pipeline to the contact sources people actually export from.

- [ ] **PR M9.1 — Relocate `ImportExtractorRouter` into its own adapter module**
  - **Scope:** Pure refactor: move `ImportExtractorRouter` out of `src/people_context/adapters/vcard_import.py` into a new `src/people_context/adapters/import_router.py`, dispatching on explicit `source_type` match (raising `ImportExtractionError("invalid_source_type", ...)` for unrecognized types instead of silently defaulting to email). Update the one call site. No new source types yet — this just prepares the ground before a third/fourth extractor lands.
  - **Touches:**
    - `src/people_context/adapters/import_router.py` (new)
    - `src/people_context/adapters/vcard_import.py` (remove `ImportExtractorRouter`, keep `VCardImportExtractor` + parsing helpers)
    - `src/people_context/adapters/mcp/server.py` (update import at the router construction site, ~lines 51/233)
    - `tests/adapters/test_import_router.py` (new)
    - `tests/adapters/test_vcard_import.py` (update import path for `ImportExtractorRouter`)
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "Router relocation")
    - Router must dispatch on explicit `source_type` match for all known types (`email`, `vcard`, and — added in later PRs — `ics`, `linkedin`); unknown types raise `ImportExtractionError("invalid_source_type", ...)` rather than silently falling through to email.
    - `ImportContent`'s constructor is unaffected — it already takes an `ImportExtractor` by Protocol (`app/import_content.py:310`), not a concrete class, so this is adapter-only.
    - `adapters/vcard_import.py` keeps only `VCardImportExtractor` and its parsing helpers after the move.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New `tests/adapters/test_import_router.py` covering dispatch for `email` and `vcard` (the two types that exist at this point) plus the unknown-`source_type` error path; existing `test_vcard_import.py` router-dependent tests updated to import from the new module and must still pass unchanged in behavior.
  - **Out of scope:** `.ics` and LinkedIn extractors themselves (PR M9.2/M9.3 add their router branches on top of this module).

- [ ] **PR M9.2 — `.ics` calendar-attendee import**
  - **Scope:** Add `IcsImportExtractor` implementing the `ImportExtractor` Protocol for `source_type="ics"`, staging one deduplicated `person` candidate per attendee email and one `interaction` candidate per event (`channel="calendar"`, fixed neutral summary, never the event `SUMMARY`/`DESCRIPTION`). Wire it into `import_router.py`. No CLI/MCP surface changes beyond accepting the existing free-string `source_type`.
  - **Touches:**
    - `src/people_context/adapters/ics_import.py` (new)
    - `src/people_context/adapters/import_router.py` (add `"ics"` branch)
    - `src/people_context/adapters/_line_folding.py` or similar shared helper module (optional extraction of `_unfold_lines`/`_parse_property`/`_decode_text` shared with `vcard_import.py`; decide per spec open question #2 and note the decision in the PR)
    - `tests/adapters/test_ics_import.py` (new)
    - `tests/adapters/test_import_router.py` (extend with `"ics"` case)
    - `tests/adapters/test_mcp_server.py` (extend in-memory `import_content` test with `source_type="ics"`)
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "`.ics` calendar import")
    - `ATTENDEE` `mailto:` + optional `CN` become `person` candidates deduplicated by normalized email **across the entire file**; differing `CN` spellings accumulate into `alternate_names`, mirroring `adapters/email_import.py:45-83`. Every event's interaction candidate references the one shared per-address candidate.
    - Email values staged as `handle` aliases, same as vCard's `EMAIL` handling.
    - One `interaction` candidate per event: `channel="calendar"`, `date` from `DTSTART`, participant refs from attendees.
    - `SUMMARY`/`DESCRIPTION` must NEVER be persisted or returned — stage a fixed neutral summary (e.g. `"Calendar event"`), same pattern as email importer's fixed `"Email correspondence"` string.
    - Per-item independence: one malformed event never blocks its neighbors; use `skipped_cards` (one-based index + reason) for skip reporting, same convention as vCard — no new skip fields.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New adapter tests in `tests/adapters/test_ics_import.py` modeled on `tests/adapters/test_vcard_import.py`: per-item independence, missing-required-field skips, self-address filtering, a raw-content sentinel (mirroring `_NOTE_SENTINEL`) asserting `SUMMARY`/`DESCRIPTION` never appear in staged candidates, and the cross-event dedup case (two events, same attendee address, different `CN` → one person candidate with both names, referenced by both interactions). App-layer: exercise through `ImportContent` against both fake ports (`tests/app/fakes.py`) and real SQLite. Extend `tests/adapters/test_mcp_server.py` in-memory test with an `.ics` `import_content` call.
  - **Out of scope:** LinkedIn extractor (PR M9.3), `init`/`demo` CLI commands (PR M9.4), any shared line-folding module refactor beyond what's needed to avoid outright duplication (may be deferred to a follow-up cleanup if it risks bloating this PR past ~1,000 lines — note the decision explicitly).

- [ ] **PR M9.3 — LinkedIn connections-export import**
  - **Scope:** Add `LinkedInImportExtractor` implementing the `ImportExtractor` Protocol for `source_type="linkedin"`, parsing "Connections.csv" rows into `person`, optional `affiliation` (Company+Position), and optional `fact` (`predicate="linkedin_connected_on"`) candidates. Wire into `import_router.py`. The profile `URL` column is intentionally not staged.
  - **Touches:**
    - `src/people_context/adapters/linkedin_import.py` (new)
    - `src/people_context/adapters/import_router.py` (add `"linkedin"` branch)
    - `tests/adapters/test_linkedin_import.py` (new)
    - `tests/adapters/test_import_router.py` (extend with `"linkedin"` case, now covering all four source types)
    - `tests/adapters/test_mcp_server.py` (extend in-memory `import_content` test with `source_type="linkedin"`)
    - `tests/adapters/test_stdio_e2e.py` (new case: `.ics` or LinkedIn fixture end-to-end stage → review → commit → `resolve_person`, per spec Testing strategy — pick whichever source isn't already covered by an E2E case)
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "LinkedIn connections-export import")
    - One `person` candidate per row (First+Last name; `EMAIL` → `handle` alias when present, same as vCard).
    - One `affiliation` candidate per row only when both Company and Position are present, using existing `AffiliationCandidateInput` shape (`org`, `role`).
    - Optional `fact` candidate (`predicate="linkedin_connected_on"`, `value=<Connected On date>`) using existing `FactCandidateInput` shape, same pattern as vCard's `BDAY`→birthday fact.
    - `URL` column is never staged as alias or fact — not a name-shaped identity signal.
    - Resolve open question #3 (strict known-header validation vs. tolerant superset) and record the decision in the PR description; fail closed on drift is the safer default absent other guidance.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New `tests/adapters/test_linkedin_import.py` modeled on `test_vcard_import.py`: per-row independence, missing-required-field skips (e.g. missing name), a raw-content sentinel if the export variant includes a free-text note field, header-set validation behavior per the decision above. Router test extended to cover all four source types plus unknown-type error path. MCP in-memory test extended. One new stdio E2E case (`tests/adapters/test_stdio_e2e.py`) covering full stage→review→commit→resolve for whichever of `.ics`/LinkedIn lacks E2E coverage yet.
  - **Out of scope:** `init`/`demo` CLI commands (PR M9.4).

- [ ] **PR M9.4 — `people-context init` and `people-context demo` CLI commands**
  - **Scope:** Add two new CLI subcommands composing only existing use cases: `init` (interactive onboarding: optional vCard import via `ImportContent`/`ReviewImport`/`CommitImport`, self-person seeding via `RememberPerson(is_self=True)`, optional communication philosophy via `SetCommunicationPhilosophy`) and `demo` (`[--reset]`, seeds a fictional dataset into a dedicated, non-default demo database path via `RememberPerson`/`SetRelationship`/`RecordInteraction`, then prints example tool invocations). No new ports or app-layer classes.
  - **Touches:**
    - `src/people_context/cli.py` (`build_parser()`: add `init` and `demo` subparsers; `main()`: dispatch; new `_cmd_init`, `_cmd_demo` functions)
    - `src/people_context/config.py` (add demo-db path resolution helper, distinct filename, same XDG-based resolution as `resolve_db_path` but never reading `--db`/`PEOPLE_CONTEXT_DB`)
    - a demo dataset fixture, e.g. `src/people_context/adapters/demo_data.py` or `tests/fixtures/demo_dataset.json` (decide per spec open question #4 — checked-in fixture vs. procedural generation; a checked-in fixture is recommended for determinism/diffability)
    - `tests/test_cli.py` (new `init` and `demo` test cases)
    - `docs/import.md` or `README.md` (document the two new commands; roadmap already gives the exact invocation forms `uv run people-context init` / `uv run people-context demo [--reset]`)
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "`people-context init`" and "`people-context demo`")
    - `init` reuses `ImportContent`→`ReviewImport`→`CommitImport` unchanged for optional vCard import; seeds self person via `RememberPersonInput(name=..., is_self=True, source="cli")`, relying on existing `SelfAlreadyExistsError`/single-self invariant; optional philosophy via `SetCommunicationPhilosophy`. No new port/app-layer class.
    - `demo` must NEVER touch the resolved `--db`/environment/config database — it resolves its own fixed demo path only (hard safety property, not convenience) and ignores `--db`/`PEOPLE_CONTEXT_DB` for its target.
    - `demo` refuses to reseed a non-empty demo database unless `--reset` is passed, checked via existing `PersonReader.list_people(include_deleted=True, limit=1)`.
    - On success `demo` prints the resolved demo database path and copy-pasteable example calls for `resolve_person`, `get_relationship_graph`, `find_connection`.
    - Both commands are CLI-only per Security section — no MCP tool triggers a full import or implicit self-person seeding.
    - Resolve open question #1 (whether `init` refuses to run twice like `demo`, or allows additive re-runs) and record the decision in the PR description.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New CLI tests in `tests/test_cli.py`: `init` with non-interactive/scripted input (covering optional-vCard-skip and optional-philosophy-skip paths, self-person seeding, `SelfAlreadyExistsError` handling on a second run); `demo` asserting it never touches a `--db`-specified path, refuses to reseed without `--reset`, and succeeds with `--reset`. App-layer: fake-port tests (`tests/app/fakes.py`'s `FakePeopleRepository`) for the `init` self-seeding and `demo` multi-use-case seeding sequence, plus real-SQLite coverage of the same paths.
  - **Out of scope:** any MCP tool changes (none are in scope for this milestone); OAuth/API-based calendar or LinkedIn integration (explicit non-goal).
## M10 — Agent utilization

> Ship prompt/skill/command content (no new tools, ports, or response fields) so agents reliably use the `resolve_person`, `get_communication_guidance`, and stage/review/commit flow that already exists.

- [ ] **PR M10.1 — Package the tool-selection + end-of-session-capture skill**
  - **Scope:** Add `skills/people-context-usage/` at the plugin root (not under `.claude-plugin/`) with a single skill document covering identity-resolution-first guidance, when to call `get_communication_guidance` vs `get_person_context`, the strict `stage_candidates` candidate vocabulary, the operator-gated-tool non-goal, and an end-of-session "review and propose staging" instruction. Does not touch `.claude-plugin/plugin.json`, server code, or add commands.
  - **Touches:**
    - `skills/people-context-usage/SKILL.md` (new)
    - `docs/claude-code-plugin.md` (mention the new skill in the local-validation doc)
  - **Spec:** m10-agent-utilization.md "Design > Skill: tool-selection guidance" and "End-of-session capture: skill-only"
    - resolve identity via `resolve_person` before assuming who a name refers to; spell out the `ambiguous` field contract
    - `get_communication_guidance` for *how*-to-communicate tasks, never conflated with `get_person_context`'s *what-is-known* shape
    - `stage_candidates` uses only the strict `person`/`interaction`/`affiliation`/`fact` vocabulary from `app/import_content.py`; raw conversation text is never a candidate field
    - explicitly states `get_sensitive_person_context`/`export_data` absence from tool discovery is expected, not a bug, when the operator hasn't set the elevation env vars
    - end-of-session instruction proposes `stage_candidates` only — never calls `commit_import`, never bypasses `review_import`
  - **Tests/validation:** `uv run ruff check .` and `uv run pytest -q` stay green (no Python changed). Extend `claude plugin validate . --strict` to cover the new `skills/` path per docs/claude-code-plugin.md#local-validation; manual local-install walkthrough exercising a scripted transcript where the agent resolves before assuming identity and proposes staging instead of fabricating a write.
  - **Out of scope:** user-invocable `/people-context:who|remember|reminders` entry points (PR M10.2); any `SERVER_INSTRUCTIONS` edit (PR M10.3).

- [ ] **PR M10.2 — Add who/remember/reminders user-invocable entry points**
  - **Scope:** Three thin user-invocable skills wrapping one existing tool call each. Depends on PR M10.1 landing (reuses the same skills-at-plugin-root pattern) but adds new files, not edits to it.
  - **Touches:**
    - `skills/people-context-who/SKILL.md` (new) → `resolve_person`, then `get_person_context` on an unambiguous match
    - `skills/people-context-remember/SKILL.md` (new) → `remember_person` for explicit assertions, `stage_candidates` for extraction from context
    - `skills/people-context-reminders/SKILL.md` (new) → `list_reminders`, optionally filtered by resolved `person_id`
    - `commands/*.md` (new, only if the plugin's minimum supported Claude Code version requires the compatibility fallback — check `.claude-plugin/plugin.json` / marketplace version constraint first)
    - `docs/claude-code-plugin.md` (document the three namespaced invocations)
  - **Spec:** m10-agent-utilization.md "Design > User-invocable who/remember/reminders"
    - actual invocations are namespaced `/people-context:who`, `/people-context:remember`, `/people-context:reminders` (plugin name `people-context`)
    - `/people-context:who` surfaces the `ambiguous`/candidate-list contract verbatim when resolution is not unique
    - `/people-context:remember` must not fabricate a write past user review when the source is agent extraction rather than explicit user assertion
    - none of the three introduce new response shapes; none call elevated (`get_sensitive_person_context`/`export_data`) tools regardless of operator env flags
  - **Tests/validation:** No Python changes expected; `uv run ruff check .` / `uv run pytest -q` unaffected. Extend the `claude plugin validate . --strict` step to cover any new `commands/` path. Manual verification per docs/claude-code-plugin.md#local-validation: install locally, `/reload-plugins`, exercise all three commands against a temporary database.
  - **Out of scope:** disambiguating "explicit assertion" vs. "extraction" automatically inside `/people-context:remember` (open question 2 in the spec) — ship the documented judgment call, don't build heuristics.

- [ ] **PR M10.3 — Extend SERVER_INSTRUCTIONS to name the under-used tools**
  - **Scope:** The only Python/versioned-code change in this milestone: append one or two sentences to `SERVER_INSTRUCTIONS` naming `get_communication_guidance` and `stage_candidates`, matching the existing style that already names `resolve_person`/`get_person_context`/`search_people`/`remember_person`. No signature, tool-annotation, or response-shape change.
  - **Touches:**
    - `src/people_context/adapters/mcp/server.py` (lines ~87-97, `SERVER_INSTRUCTIONS`)
    - `tests/adapters/test_mcp_server.py` and/or `tests/adapters/test_mcp_entrypoint.py` (update any assertion on the literal string)
    - `docs/mcp-interface.md` (if it quotes `SERVER_INSTRUCTIONS` verbatim)
  - **Spec:** m10-agent-utilization.md "Design > SERVER_INSTRUCTIONS extension (optional, minimal)"
    - plain-string edit only, inside an adapter module — no port/domain/app impact
    - must not alter `stage_candidates`'s write-approval `ToolAnnotations` (stays non-`readOnlyHint`)
    - must not mention or encourage `get_sensitive_person_context`/`export_data`
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` green including updated string assertions in `tests/adapters/test_mcp_server.py`.
  - **Out of scope:** any tool annotation change; this PR may ship independently of M10.1/M10.2 per the spec's open question 3, but is ordered last here since it is the smallest, lowest-risk slice.

## M11 — Sync bundle export and trusted bootstrap restore

> Give the M6 changelog a first consumer: a CLI-only bundle export/import that hands a device's complete state to a brand-new, still-empty device — not incremental two-way replay, which stays deferred.

- [ ] **PR M11.1 — Widen `Changelog.list_entries` to support unbounded reads**
  - **Scope:** Additive Protocol change only: `limit: int = 100` becomes `limit: int | None = 100`, where `None` means "all entries." Updates the SQLite implementation and any fakes. No new port, no CLI change beyond what already calls this method.
  - **Touches:**
    - `src/people_context/ports/changelog.py` (`Changelog.list_entries` signature)
    - `src/people_context/adapters/sqlite/changelog.py` (skip the `LIMIT` clause when `limit is None`)
    - any fake `Changelog` implementation under `tests/` used by app-layer tests
    - `tests/adapters/test_sqlite_changelog.py`
  - **Spec:** m11-sync-bundle-and-bootstrap-restore.md "Design > Bundle contents and envelope"
    - backward-compatible: every existing caller (`sync-log` CLI) passes an explicit int or relies on the unchanged default of 100
    - `None` must return every changelog row, ordered by the existing deterministic key `(hlc_physical_ms, hlc_logical, device_id, op_id)`
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` green. Extend `tests/adapters/test_sqlite_changelog.py` with an unbounded-listing case alongside the existing bounded case; confirm `_cmd_sync_log` behavior is unchanged (still passes an explicit int/default).
  - **Out of scope:** the bundle reader/exporter that will consume `limit=None` (PR M11.2).

- [ ] **PR M11.2 — Bundle export: `BundleReader`, `ExportSyncBundle`, `sync push` CLI**
  - **Scope:** A new narrow read port that performs the domain snapshot, relationship vocabulary, complete changelog, device rows, and HLC watermark reads inside one `SqliteUnitOfWork` transaction, a pure app-layer use case that assembles the versioned `SyncBundle` envelope from it, and the `people-context sync push --output DIR` CLI command that writes it as owner-only-permission JSON. No restore/write path in this PR.
  - **Touches:**
    - `src/people_context/ports/sync_bundle.py` (new) — `BundleReader` Protocol, one method, plus the `SyncBundle` envelope shape (or define the Pydantic model in `app/sync_bundle.py` per the one-module-per-use-case convention)
    - `src/people_context/adapters/sqlite/bundle_reader.py` (new) — `SqliteBundleReader`, single-transaction read reusing `ExportReader`'s row shapes and `SqliteChangelog.list_entries(limit=None)` internally (joins the already-open outer transaction)
    - `src/people_context/app/sync_bundle.py` (new) — `ExportSyncBundle` use case (read-only, no `audit_mutation`/UoW needed here — the consistency guarantee lives in the adapter)
    - `src/people_context/cli.py` — `sync` subparser, `push` subcommand, `_cmd_sync_push` following the `_cmd_export` 0o600-permission pattern (`cli.py:366-376`)
    - `docs/cli.md`, `docs/privacy-and-safety.md` (bundle is plaintext JSON — same posture as `export`, call this out prominently)
    - `tests/adapters/test_sqlite_bundle_reader.py` (new), `tests/app/test_sync_bundle.py` (new, fake-port), `tests/test_cli.py` (sync push cases)
  - **Spec:** m11-sync-bundle-and-bootstrap-restore.md "Design > Bundle contents and envelope"
    - envelope: `format="people-context-sync-bundle"`, `version=1`, `created_at`, `origin_device_id`, `watermark`, `devices`, `snapshot`, `relationship_vocabulary`, `changelog` (exact shape in spec's JSON example)
    - `devices` collection includes every device referenced by any changelog entry plus the active origin device even with zero operations
    - `relationship_vocabulary` includes both seeded and custom rows from both vocabulary tables (an omission `people-context export` also has today, not fixed here — out of scope)
    - the read must be one transaction end-to-end — two independent adapter reads (as `read_export()` does today) is the exact bug this PR avoids
    - `push` writes `DIR/people-context-sync-bundle.json` with `os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`, mirroring `_cmd_export`
  - **Tests/validation:** fake-`BundleReader` test for `ExportSyncBundle` envelope shape in `tests/app/`; `tests/adapters/test_sqlite_bundle_reader.py` asserting all collections come from one transaction (a second-connection write during the read must not appear partially — same style as `tests/adapters/test_sqlite_unit_of_work.py`); `tests/test_cli.py` covers `sync push` success output (bundle path, entity counts, changelog count, watermark) and the 0o600 permission check. `uv run ruff check .` clean, `uv run pytest -q` green.
  - **Out of scope:** `sync pull`/restore (PR M11.3); any MCP tool (this stays CLI-only per spec, matching `export-vault`'s posture).

- [ ] **PR M11.3 — Bootstrap restore: `BootstrapRestorer`, `RestoreSyncBundle`, `sync pull` CLI (largest PR — keep tightly scoped to restore only)**
  - **Scope:** The atomic, verbatim bulk-write restore path targeting only a freshly initialized, empty database, plus the `people-context sync pull --input PATH [--yes]` CLI command with preview-before-destructive-action confirmation. Depends on PR M11.2 for the `SyncBundle` envelope shape it consumes.
  - **Touches:**
    - `src/people_context/ports/bootstrap_restore.py` (new) — `BootstrapRestorer` Protocol, one method
    - `src/people_context/adapters/sqlite/bootstrap_restore.py` (new) — `SqliteBootstrapRestorer`
    - `src/people_context/app/sync_bundle.py` — add `RestoreSyncBundle` use case alongside `ExportSyncBundle`, with a structured refusal error (`code`/`message`/`details`, mirroring `ImportPipelineError`)
    - `src/people_context/cli.py` — `pull` subcommand, `_cmd_sync_pull` with preview counts (mirroring `PreviewForget`/`_cmd_delete`'s pattern) and `--yes`/interactive confirmation
    - `docs/cli.md` (refusal message text), `docs/privacy-and-safety.md` (audit log travels verbatim/unredacted except already-redacted forget payloads)
    - `tests/adapters/test_sqlite_bootstrap_restore.py` (new), `tests/app/test_sync_bundle.py` (extend with fake-port restore-refusal case), `tests/test_cli.py` (sync pull cases)
  - **Spec:** m11-sync-bundle-and-bootstrap-restore.md "Design > New port and adapter for verbatim bulk restore" (full 10-step sequence)
    - single transaction opened with `BEGIN IMMEDIATE` (not the plain deferred `BEGIN` `SqliteUnitOfWork` normally issues) so the write reservation is acquired *before* the emptiness checks — avoids `SQLITE_BUSY_SNAPSHOT` races under WAL
    - emptiness checks (no person rows including soft-deleted, no changelog entries) run only inside that reservation, never as a separate pre-check
    - order matters for FK integrity: vocabulary reconciliation → device rows (all `retired_at` forced set) → domain rows incl. `audit_log` verbatim → changelog rows → FTS rebuild (`PersonSearchIndexer.rebuild_person_search()`) → `HybridLogicalClock.observe()` past the bundle watermark → commit
    - vocabulary reconciliation is per-row: byte-identical existing PK → skip; differing existing PK → reject whole bundle, roll back; new PK → insert
    - **imported device identities are never active** — every bundle device row lands with `retired_at` forced set so `SqliteHybridLogicalClock._device_row()` can only ever select the destination's own freshly created device
    - bundle reference validation: every changelog `device_id` must exist in the bundle's own `devices` collection, checked before any write
    - only the optional semantic reindex (`reindex --semantic`) stays outside this transaction
    - `pull` refuses (exit 1, no writes) with a structured message pointing at bootstrap-only scope when the target already has primary data or changelog history
  - **Tests/validation:** `tests/adapters/test_sqlite_bootstrap_restore.py` covering full round-trip write of every table (both vocabulary tables included), atomicity under a forced failure at any phase (including FTS rebuild and HLC advancement) leaving an empty DB, imported-device-rows-all-retired plus `SqliteHybridLogicalClock.device_id` returns the destination's id, correct HLC advancement past the watermark, vocabulary reconciliation (default-only skip case and differing-row rejection case), emptiness-check concurrency in both orderings (pre-`BEGIN IMMEDIATE` commit is seen; post-reservation write blocks/times out), and bundle reference validation (dangling `device_id` rejected, rollback to empty). `tests/test_cli.py` covers the non-empty-target refusal path and 0o600 permission check. `uv run ruff check .` clean, `uv run pytest -q` green.
  - **Out of scope:** incremental/two-way replay into an already-diverged database (`sync pull` always refuses this); `sync_conflicts` table usage; any encryption of the bundle file; the E2E round-trip test (PR M11.4).

- [ ] **PR M11.4 — Multi-device E2E round trip and doc sign-off**
  - **Scope:** End-to-end proof that push-from-A/pull-into-B preserves content and vocabulary parity and correct HLC continuity for subsequent writes, plus a multi-device-chain adapter test. Depends on PR M11.2 and PR M11.3 both merged. No new production code expected beyond incidental fixes surfaced by the E2E test.
  - **Touches:**
    - `tests/adapters/test_stdio_e2e.py` (new case, following `test_real_stdio_graph_then_cli_vault_export_uses_matching_links`'s pattern)
    - `tests/adapters/test_sqlite_bootstrap_restore.py` (add the A→B→C multi-device-history case if not already covered in PR M11.3)
    - `docs/roadmap.md` status line update if the milestone is being marked delivered here
  - **Spec:** m11-sync-bundle-and-bootstrap-restore.md "Testing strategy" (E2E bullet) and "Design" multi-device paragraph
    - build device A via the real stdio server, add a custom relationship type with inverse + synonym, record people/relationships/interactions including one custom-type relationship, `sync push`, then a fresh device B, `sync pull`
    - assert `people-context export` output is content-equal between A and B (excluding volatile fields like `exported_at`)
    - custom vocabulary behaves identically on B: synonym resolution on write, inverse `display_type` in `get_person_context`, canonical edges in `get_relationship_graph`
    - a subsequent write on B produces a changelog entry whose HLC sorts after every entry pulled from A and carries B's own `device_id`
    - multi-device chain: a bundle whose changelog spans two device ids restores both device rows retired with correct provenance, and a fresh bundle exported from the restored database carries both forward
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` fully green including the new stdio E2E case. This is the milestone's final acceptance gate — all M11.1-M11.3 tests plus this E2E case passing is the definition of "M11 done."
  - **Out of scope:** any further phasing of incremental replay or `sync_conflicts` (explicitly deferred milestones); bundle schema/migration-version validation (open question 1) and at-rest encryption (open question 3) are not addressed here.
## M12 — Trust, stability, and v1.0

> Formalize the compatibility discipline the project has followed since M7 into a stated promise, and close the two named trust gaps (opt-in at-rest encryption, a factual cloud-memory-tool threat-model comparison) without changing any existing behavior.

- [ ] **PR M12.1 — Publish the MCP/DB compatibility promise doc**
  - **Scope:** Add a new `docs/compatibility.md` stating the response-contract, DB-schema, and CLI compatibility rules; link it from `README.md`'s docs table and from `docs/mcp-interface.md`. Documentation only — no code changes.
  - **Touches:** `docs/compatibility.md` (new), `README.md` (docs table entry), `docs/mcp-interface.md` (cross-link near existing contract mentions).
  - **Spec:** distilled from `docs/specs/m12-trust-stability-v1.md` "Compatibility promise" section:
    - MCP: existing response fields never removed/repurposed; new fields additive only (cite the `duplicate_relationships_removed` precedent); tool names and required params stable; optional params may be added.
    - DB: migrations forward-only and additive within a major version, per the existing `001_initial.sql`–`004_curation_indexes.sql` pattern and the `PRAGMA user_version` gate in `db.py::_run_migrations`.
    - CLI: existing subcommands/flags keep working; new flags additive with backward-compatible defaults (cite `reindex --semantic`).
    - Explicitly state the vault-export Markdown layout's status relative to the promise (resolve Open Question 2 — recommend: exclude it explicitly as "deterministic but not yet a frozen contract" unless the user says otherwise).
    - Do not commit to a specific deprecation-window policy beyond what's already true (Open Question 3) — keep it descriptive of current practice, not aspirational.
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` green (no behavior touched, so this is a regression guard); manual link-check that every new cross-reference in `README.md`'s docs table resolves to a real file (spec's stated validation for the docs-only slice).
  - **Out of scope:** version bump and release-checklist update (PR M12.2); the threat-model comparison subsection (PR M12.3); README "Demo" section (can ride with M12.3 or its own follow-up, see M12.3 scope).

- [ ] **PR M12.2 — Bump to 1.0.0 and update the release checklist**
  - **Scope:** Bump `project.version` in `pyproject.toml` to `1.0.0`, change the classifier from `"Development Status :: 3 - Alpha"` to `"Development Status :: 5 - Production/Stable"`, and add one checklist item to `docs/releasing.md` requiring the compatibility-promise doc (from M12.1) to be published before tagging. Depends on M12.1 being merged first so the checklist item references a real doc.
  - **Touches:** `pyproject.toml` (`project.version`, classifiers), `docs/releasing.md` ("Publish a release" section).
  - **Spec:** from `docs/specs/m12-trust-stability-v1.md` "Version and release checklist":
    - Follow the exact existing procedure in `docs/releasing.md#publish-a-release` (update version → merge → tag → GitHub Release → approve `pypi` environment) — do not redesign the release workflow.
    - Add exactly one new checklist item: confirm `docs/compatibility.md` is published before the tag.
    - No other pyproject changes in this PR (the `encrypted` extra is PR M12.4's concern, not this one's).
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` green; `tests/test_packaging_metadata.py` (existing) must still pass against the new version/classifier — check whether it asserts an exact version string and update the assertion in lockstep if so.
  - **Out of scope:** SQLCipher extra and any dependency additions; actually cutting the 1.0.0 release/tag (that's a post-merge human action, not part of this PR's diff).

- [ ] **PR M12.3 — Threat-model comparison and README demo polish**
  - **Scope:** Append a factual, sourced subsection to the existing "Threat model notes" heading in `docs/privacy-and-safety.md` comparing this project's local-first model against cloud-hosted memory tools (mem0, Zep, similar); add a short "Demo" section to `README.md` between "Why" and "Quick start" walking through `people-context demo` (M9). Documentation only.
  - **Touches:** `docs/privacy-and-safety.md` (new subsection under "Threat model notes", after the existing SQLCipher "Future option" bullet at line ~167), `README.md` (new "Demo" section + any screenshot/GIF asset reference).
  - **Spec:** from `docs/specs/m12-trust-stability-v1.md` "Threat-model comparison" and "README polish":
    - Compare on exactly these axes: where data is stored at rest, what a vendor breach/subpoena can expose, whether the tool works fully offline, what "delete my data" means in each model vs. this project's hard `forget` semantics (`docs/privacy-and-safety.md#forget-vs-soft-delete`).
    - Match the existing bullet-list style of "Threat model notes"; stay factual/sourced, not marketing copy (resolve Open Question 4 by adding a dated "as of" note rather than an open-ended claim).
    - README demo section needs only a GIF/screenshot sequence plus a walkthrough of `people-context demo`; no new CLI behavior.
  - **Tests/validation:** `uv run ruff check .` clean; `uv run pytest -q` green (no code touched); link-check any new asset paths/cross-references resolve.
  - **Out of scope:** any change to `people-context demo` itself (M9-owned); SQLCipher (PR M12.4).

- [ ] **PR M12.4 — Opt-in SQLCipher at-rest encryption**
  - **Scope:** Add an `encrypted` optional-dependency extra, a new `adapters/sqlite/db.py::open_encrypted_db(path, key)` entrypoint distinct from `open_db`, and `--encrypted` flags on both the MCP server (`adapters/mcp/server.py::build_server()`/`_build_parser()`) and CLI (`cli.py::_open_context()`), each requiring `PEOPLE_CONTEXT_DB_KEY` in the environment. This is the largest PR of the milestone; keep it to just the open-path plus wiring, no schema changes.
  - **Touches:** `pyproject.toml` (`[project.optional-dependencies].encrypted`), `src/people_context/adapters/sqlite/db.py` (new `open_encrypted_db`), `src/people_context/adapters/mcp/server.py` (`--encrypted` flag, key-presence check, refusal path), `src/people_context/cli.py` (`_open_context()` equivalent flag/env check), `tests/adapters/test_sqlite_encryption.py` (new), CLI test file for the refusal case (e.g. `tests/test_cli.py` or a new small module).
  - **Spec:** from `docs/specs/m12-trust-stability-v1.md` "Opt-in SQLCipher" and "Security/privacy considerations":
    - `open_db`'s existing signature, defaults, and every existing real-SQLite test remain completely unchanged — `open_encrypted_db` is a new, separate function, not a parameter added to `open_db`.
    - No adapter beyond `db.py` changes: every repository/store already operates on an opaque `sqlite3.Connection`.
    - Key sourced **only** from `PEOPLE_CONTEXT_DB_KEY` env var, never a CLI/flag value (same reasoning as `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE`/`_ENABLE_EXPORT` in `adapters/mcp/security.py:process_elevation_enabled`) — a flag value leaks via shell history/`ps`.
    - `--encrypted` without `PEOPLE_CONTEXT_DB_KEY` present: clear refusal to start, never a silent plaintext fallback, in both server and CLI entrypoints.
    - Key must never be logged, echoed, or written into any error message, audit payload, or changelog payload.
    - Verify (don't assume) that the chosen SQLCipher binding encrypts `-wal`/`-shm` companion files, not just the main db file.
    - Resolve Open Question 1 by picking `sqlcipher3-binary` unless investigation surfaces a blocker (prebuilt wheels, matches the spec's stated preference ordering) and record the exact pinned version range in the extra.
    - Docs touched only to the extent of not implying encryption is now default anywhere (verify no accidental wording change outside this PR's new content).
  - **Tests/validation:** new `tests/adapters/test_sqlite_encryption.py` covering: `open_encrypted_db` requires a key; rejects empty/missing key with a clear error; rejects wrong key against a previously-encrypted file; a plain unkeyed `sqlite3.connect()` cannot read table contents from an encrypted file (the actual verification encryption does something). CLI test for `--encrypted` without `PEOPLE_CONTEXT_DB_KEY` asserting clear refusal. All ~three dozen existing `tests/adapters/*` files calling `open_db(...)` must pass unmodified. `uv run ruff check .` clean; `uv run pytest -q` fully green, including the `encrypted` extra installed in CI (confirm CI matrix installs the extra so this path is actually exercised, not skipped).
  - **Out of scope:** key rotation, OS-keychain integration, multi-key support (explicitly deferred past this milestone per spec Non-goals); making encryption default; any MCP tool or response-shape change (none needed — server/CLI flags only).

## M13 — Daily utility & proactive signals

> Add read-only recency, date-awareness, meeting-prep, iCalendar export, and changelog-tail features over data the schema already holds, with zero new writes or recorded data.

- [ ] **PR M13.1 — `get_stale_relationships` MCP tool + `people-context stale` CLI**
  - **Scope:** New narrow read port `ports/insights.py::RecencyReader`, its SQLite implementation `adapters/sqlite/recency_reader.py`, app use case `app/get_stale_relationships.py`, MCP tool registration, and the `people-context stale` CLI report. Read-only; no migration expected unless `EXPLAIN QUERY PLAN` shows a table scan.
  - **Touches:** `src/people_context/ports/insights.py` (new — `RecencyReader` protocol), `src/people_context/adapters/sqlite/recency_reader.py` (new), `src/people_context/app/get_stale_relationships.py` (new), `src/people_context/adapters/mcp/tools/` (new module or addition to an existing tools module, wired through `ToolDeps` in `build_server()`), `src/people_context/cli.py` (`stale` subcommand), `tests/app/fakes.py` (extend for `RecencyReader` fake), `tests/adapters/test_sqlite_recency_reader.py` (new), `tests/adapters/test_mcp_server.py` (extend annotation assertions).
  - **Spec:** from `docs/specs/m13-daily-utility.md` "`get_stale_relationships`" section:
    - Recency computed only over `public`/`personal` interactions — same ordinary sensitivity boundary as `_can_disclose` in `app/get_person_context.py`; a person whose only interactions are elevated reports `last_interaction_at: null`, identical to a person with none. Do not add an elevated variant in this PR (deferred, Open Question 2).
    - One row per person with `categories: [...]` (a list, not scalar) — matches multiple simultaneous active relationship rows scoped by `(subject, object, type)`; `category` filter param matches any element.
    - Params: optional `category`, optional `threshold_days` (default 90), `limit` capped at 100 with `truncated` flag — same explicit-caps convention as the M7 graph tools (`app/relationship_graph.py`'s `truncated` field).
    - Response shape exactly as specced: `{"people": [{"person_id", "name", "categories", "last_interaction_at", "days_since", "interaction_count"}], "truncated"}` — additive-only per the M12 promise.
    - People with zero ordinary-disclosure interactions sort first with `last_interaction_at: null`. No summaries/facts/interaction content disclosed — names, categories, recency metadata only.
    - MCP tool registered `readOnlyHint=true` via `ToolAnnotations` (see `_READ_ONLY` pattern in `adapters/mcp/tools/graph.py`).
  - **Tests/validation:** app-layer fake-port tests for threshold/cap/zero-interaction-sort-first behavior; sensitivity-boundary test (latest interaction `restricted` → falls back to latest `public`/`personal` date, `null` if none, restricted row affects neither date nor count); multi-category-to-self test (one row, all categories, counted once against `limit`); adapter test confirming soft-deleted people excluded, `interaction_participants` join resolves correct latest date, sensitivity filtering happens in SQL not post-hoc; MCP in-memory server test for tool contract/annotations; CLI snapshot test for `stale`. `uv run ruff check .` clean; `uv run pytest -q` green.
  - **Out of scope:** `upcoming_dates` (PR M13.2); any elevated/operator variant; relationship "health" scoring (explicit non-goal).

- [ ] **PR M13.2 — `upcoming_dates` MCP tool + `people-context upcoming` CLI**
  - **Scope:** App use case `app/list_upcoming_dates.py` composing the existing `ContextReader.list_facts` port method and the existing `ListReminders` use case (`app/list_reminders.py`); new MCP tool and `people-context upcoming` CLI report. No new port needed (reuses `ContextReader`/`ListReminders`).
  - **Touches:** `src/people_context/app/list_upcoming_dates.py` (new), MCP tool registration alongside `get_stale_relationships`'s module from M13.1, `src/people_context/cli.py` (`upcoming` subcommand), `tests/app/` (new test module), `tests/adapters/test_mcp_server.py` (extend), CLI test additions.
  - **Spec:** from `docs/specs/m13-daily-utility.md` "`upcoming_dates`" section:
    - Fact qualifies only if `sensitivity` passes the ordinary `public`/`personal` boundary (same as `get_person_context`) — a sensitive/restricted birthday fact must be entirely invisible, since the fact's `value` *is* the date (no partial-disclosure mitigation applies).
    - `predicate` must be date-like — initially exactly `birthday`; value must parse as ISO date or recurring `--MM-DD`; unparseable values are skipped and counted in `skipped_unparseable`, never guessed (resolves Open Question 3 by shipping exactly one predicate for now).
    - Reminders qualify when `status` active and `due_at` inside the window.
    - Params: `window_days` (default 30, capped at 366), optional `person_id`.
    - Response: list of `{person_id, name, kind: "birthday"|"reminder", date, label}` ordered by date, plus `skipped_unparseable` count.
    - MCP tool `readOnlyHint=true`.
  - **Tests/validation:** app-layer fake-port tests for ISO/`--MM-DD`/unparseable-skip-counting date parsing; sensitivity-boundary test (restricted birthday fact never appears in output nor in `skipped_unparseable`); MCP in-memory server test for contract/annotations; CLI snapshot test for `upcoming`; one E2E stdio case (per spec "Testing strategy") recording interactions/reminders then asserting `stale` and `upcoming` CLI output against the same data through MCP context reads — this can be the single E2E test covering both M13.1 and M13.2 tools, added in whichever of the two PRs lands second (this one). `uv run ruff check .` clean; `uv run pytest -q` green.
  - **Out of scope:** additional date-like predicates beyond `birthday` (e.g. `anniversary` — Open Question 3, deferred); elevated variants; meeting-prep skill content (PR M13.3).

- [ ] **PR M13.3 — Meeting-prep skill content + `reminders-ics` export**
  - **Scope:** Two independent, small, plugin/CLI-only deliverables bundled for size: (a) a meeting-preparation section added to the existing M10 skill (prompt content only, zero server code); (b) `people-context reminders-ics --output FILE`, a CLI-only deterministic iCalendar export of reminders.
  - **Touches:** M10 skill/plugin content file(s) (locate the existing M10 skill directory, e.g. under a `plugin/` or `.claude/skills/` path used by M10 — add a section, do not restructure), `src/people_context/cli.py` (`reminders-ics` subcommand), a new small serialization helper (e.g. `src/people_context/adapters/ics_export.py` or inline in `cli.py` if short enough), `tests/test_cli.py` or new `tests/test_reminders_ics.py`.
  - **Spec:** from `docs/specs/m13-daily-utility.md` "Meeting preparation" and "`people-context reminders-ics`":
    - Skill addition: instruct the agent, when preparing for a meeting or an `.ics` import is in play, to resolve each attendee (`resolve_person`), fetch `get_person_context` + `get_communication_guidance`, list open reminders per attendee, and compose a brief. Zero new tools.
    - iCalendar export: one `VTODO` per exported reminder via `RecordStore.list_reminders` filters — `DUE` from `due_at`, `SUMMARY` from reminder text, `UID` from the reminder's ULID id — sorted by `(due_at, id)`, written with the existing `0o600` owner-only pattern `_cmd_export` already uses.
    - Only dated reminders exported; report a `skipped_undated` count (undated notes already surface via `get_communication_guidance`).
    - `recurrence` maps to `RRULE` only for exactly `yearly`/`monthly`/`weekly` → `FREQ=YEARLY|MONTHLY|WEEKLY`; anything else non-empty is exported as a single dated occurrence and counted in `skipped_unmapped_recurrence`, never guessed.
    - Determinism: identical data yields byte-identical output — fixed `DTSTAMP` derived from each reminder's own timestamps, never wall-clock time (mirrors vault export's determinism guarantee).
    - CLI-only: file-writing operations are never model-callable (consistent with JSON/vault export).
  - **Tests/validation:** CLI tests for `reminders-ics` byte-determinism (two runs, identical bytes), `0o600` permission check, `skipped_undated`/`skipped_unmapped_recurrence` counting, and the three supported `RRULE` mappings. No server-side tests needed for the skill content beyond confirming the plugin file is well-formed/loadable if the repo has such a check. `uv run ruff check .` clean; `uv run pytest -q` green.
  - **Out of scope:** `VEVENT`-based mapping (Open Question 5 — ship `VTODO` as specced); any push integration with third-party task managers (explicit non-goal); `watch` (PR M13.4).

- [ ] **PR M13.4 — `people-context watch` changelog tail**
  - **Scope:** Add one additive port method `Changelog.list_entries_after(cursor, limit)` (ascending order, strictly after a cursor tuple) and the CLI-only `people-context watch` command that polls it and emits JSON lines to stdout. No new tables/columns expected.
  - **Touches:** `src/people_context/ports/changelog.py` (add `list_entries_after` to the `Changelog` protocol — additive, existing `list_entries` unchanged), `src/people_context/adapters/sqlite/changelog.py` (implement it), `src/people_context/cli.py` (`watch` subcommand, `--interval` default 2, `--from-start`), `tests/adapters/test_sqlite_changelog.py` (extend), CLI test for `watch`.
  - **Spec:** from `docs/specs/m13-daily-utility.md` "`people-context watch`" section:
    - Use the existing deterministic ordering key `ChangelogEntry.comparison_key()` = `(hlc_physical_ms, hlc_logical, device_id, op_id)` (`ports/changelog.py`) for cursor comparison.
    - `list_entries_after(cursor, limit)` returns entries strictly after the cursor in ascending key order; the existing `list_entries` (descending, used by `sync-log`) is unchanged — this is a new, additive method, not a modified signature.
    - Command polls at `--interval` seconds (default 2), emits one JSON line per entry, persists no state (cursor lives in process memory only); `--from-start` replays from the beginning.
    - Output to stdout only; must make no network calls — this must be explicit in code and docs since changelog payloads are personal data (`docs/design/sync.md#21-payloads-are-intentionally-lossy`).
  - **Tests/validation:** adapter test for `list_entries_after` ascending-cursor coverage including cross-device HLC ties (`tests/adapters/test_sqlite_changelog.py`); CLI test asserting `watch` emits exactly the entries written after its cursor in one poll cycle. `uv run ruff check .` clean; `uv run pytest -q` green.
  - **Out of scope:** a notification daemon or any background/always-on process (explicit non-goal — `watch` only runs while invoked); `--follow=false` one-batch-then-exit scripting sugar (Open Question 4, defer unless requested); any network sink.
## M14 — Ecosystem & interoperability

> Meet adjacent ecosystems (chat surfaces, vCard-based address books, Outlook/WhatsApp exports, Obsidian) where their users already are, without adding a new MCP tool or database write path.

- [ ] **PR M14.1 — CLI person brief + `list --json`**
  - **Scope:** Add `app/compose_person_brief.py` composing `GetPersonContext` + `GetCommunicationGuidance` + `ListReminders` into one deterministic markdown/JSON brief, and the `people-context brief PERSON` CLI command. Also add `--json` to the existing `list` command (needed as the Obsidian plugin's future person index). Does not touch `export-vcard`, importers, or the plugin.
  - **Touches:**
    - `src/people_context/app/compose_person_brief.py` (new)
    - `src/people_context/cli.py` (`brief` subcommand, `list --json`)
    - `tests/app/test_compose_person_brief.py` (new), `tests/adapters/test_cli*.py` or equivalent CLI test module
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"`people-context brief`")
    - `ListReminders`' person filter is required, not optional — it's the only way to surface `follow_up`/`occasion` reminders, since context/guidance include only `communication_note` ones.
    - Sensitivity mirrors vault export exactly: elevated material needs explicit `--include-sensitive`; footer states the text is outside server disclosure controls.
    - Output: stdout by default (markdown), `--json` for the stable machine form, or `--output FILE` written with the `0o600` convention (see `cli.py`'s existing `os.open(..., 0o600)` pattern).
    - No MCP tool — CLI-only, per the non-goals list (keeps bulk-disclosure formatting human-operated).
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. Fake-port tests for `ComposePersonBrief` covering composition, sensitivity gating, deterministic ordering, and all open-reminder kinds. CLI snapshot test for `brief` including the `--include-sensitive` diff and `0o600` file-mode check. `list --json` shape test.
  - **Out of scope:** brief style templates (open question 2, deferred); `export-vcard`; any MCP-facing change.

- [ ] **PR M14.2 — `export-vcard` deterministic writer**
  - **Scope:** New `adapters/filesystem/vcard_writer.py` mirroring `vault_writer.py`'s determinism rules, plus the `people-context export-vcard` CLI command. Inverts the existing vCard importer's field mapping. Does not add any new import source.
  - **Touches:**
    - `src/people_context/adapters/filesystem/vcard_writer.py` (new)
    - `src/people_context/app/export_vcard.py` (new, thin use case parallel to `app/export_vault.py`)
    - `src/people_context/cli.py` (`export-vcard` subcommand)
    - `tests/adapters/test_vcard_export.py` (new)
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"`people-context export-vcard`")
    - Stable person/property ordering; byte-identical re-export over unchanged data (same guarantee as `docs/vault-export.md`).
    - Field mapping: `FN`/`N` ← canonical name, `NICKNAME` ← `nickname` aliases, `EMAIL` ← `handle` aliases parsing as addresses, `ORG`/`TITLE` ← active affiliations, `BDAY` ← `predicate="birthday"` facts (`AliasKind` in `domain/person.py`).
    - `--version {3.0,4.0}` dialect flag; everything emitted must round-trip through the existing `VCardImportExtractor` — enforced by a round-trip test, not just eyeballed.
    - Elevated-sensitivity facts follow the same `--include-sensitive` gate as vault export and `brief`.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. `test_vcard_export.py`: determinism (byte-identical re-export), sensitivity gating, and a full round trip through `VCardImportExtractor` asserting people/aliases/affiliations/birthday facts survive. CLI `0o600` and determinism checks.
  - **Out of scope:** CardDAV (explicit non-goal); Outlook/WhatsApp import; any change to the importer side of vCard.

- [ ] **PR M14.3 — Outlook CSV + WhatsApp import extractors**
  - **Scope:** Relocate `ImportExtractorRouter` out of `adapters/vcard_import.py` into its own `adapters/import_router.py` (if the M9 relocation hasn't already landed on this branch — check first, this PR must not duplicate it), then add `OutlookImportExtractor` and `WhatsAppImportExtractor` plus the `self_names`/`self_sender` self-resolution mechanism. Does not add any new MCP tool; `import_content` gains only the two `source_type` values and one additive optional parameter.
  - **Touches:**
    - `src/people_context/adapters/import_router.py` (new/relocated `ImportExtractorRouter`)
    - `src/people_context/adapters/outlook_import.py` (new `OutlookImportExtractor`)
    - `src/people_context/adapters/whatsapp_import.py` (new `WhatsAppImportExtractor`)
    - `src/people_context/app/import_content.py` (`ImportContent._self_addresses` sibling `self_names`; pass-through `self_sender` kwarg)
    - `src/people_context/adapters/mcp/tools/imports.py`, `ports/imports.py` (additive `self_sender` parameter)
    - `tests/adapters/test_outlook_import.py`, `tests/adapters/test_whatsapp_import.py` (new), extend `tests/adapters/test_import_router.py` (or equivalent) dispatch matrix
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"Outlook CSV and WhatsApp import extractors")
    - Outlook: maps First/Middle/Last/E-mail/Company/Job Title/Birthday columns to `person`+`handle` alias, `affiliation` (org, role), `birthday` fact; tolerate a superset of columns, skip rows missing required fields with per-row skip reasons (same pattern as the M9 LinkedIn open question).
    - WhatsApp: parse **only** the `[date, time] Sender Name:` prefix of each line — message text after `: ` is never read into any candidate field. Senders dedupe by normalized name into `person` candidates; one `interaction` candidate per calendar day per chat, channel `"whatsapp"`.
    - Self-resolution: `ImportContent` derives `self_names` (normalized canonical name + every alias value, via `normalize_name`) and passes it as a new keyword existing extractors ignore; WhatsApp additionally accepts an optional `self_sender` hint. Senders matching either signal are excluded from `person` candidates and marked self-participation in the day's interaction.
    - Everything stages through the unchanged `import_content` → `review_import` → `commit_import` gate — no schema/migration change.
    - Raw-content rule: enforced by a sentinel test (unique string in message bodies must appear in no staged candidate), matching the `_NOTE_SENTINEL` pattern in existing vCard tests.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. `test_outlook_import.py`/`test_whatsapp_import.py` modeled on `test_vcard_import.py` (per-row/line independence, skip reasons, cross-file dedup, sentinel). WhatsApp fixtures covering self under a matching alias (nickname/native-script/transliteration), under "You", and under a phone number with `self_sender` set — self person must never appear as a staged `person` candidate in any case. One stdio E2E case committing a WhatsApp import and asserting the sentinel never reaches `get_person_context` output.
  - **Out of scope:** Signal import (non-goal, no stable plaintext export); the `--format` hint flag for WhatsApp locale variants (open question 3 — ship one detected format first, extend later if needed).

- [ ] **PR M14.4 — Obsidian plugin (`obsidian-plugin/`)**
  - **Scope:** New in-repo TypeScript package under `obsidian-plugin/`, structured like `openclaw-plugin/` (own `package.json`, `tsconfig.json`, `vitest`), rendering read-only person panes by shelling out to the CLI's `--json` output. Adds the CI mirror-to-distribution-repo workflow. Does not add any Python/server code and does not open the SQLite database.
  - **Touches:**
    - `obsidian-plugin/package.json`, `obsidian-plugin/tsconfig.json`, `obsidian-plugin/vitest.config.ts`, `obsidian-plugin/manifest.json`
    - `obsidian-plugin/src/index.ts` (or `main.ts`), `obsidian-plugin/src/cli-bridge.ts`, `obsidian-plugin/src/*.test.ts`
    - `.github/workflows/obsidian-plugin-mirror.yml` (new, modeled on `.github/workflows/package-publish.yml`)
    - `obsidian-plugin/README.md`
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"Obsidian plugin (`obsidian-plugin/`)")
    - Data access is CLI-only: `people-context list --json` for the person index (PR M14.1), `people-context brief PERSON --json` for pane content (PR M14.1). The plugin never opens the database file and never passes `--include-sensitive` — elevated material is unreachable from Obsidian by construction.
    - Plugin declares itself desktop-only in `manifest.json` (it spawns a local process) and degrades to a "CLI not found — install people-context" state with a configurable binary-path setting.
    - Panes: identity, relationships with `display_type`, ordinary-disclosure facts, recent interactions, open reminders — same naming/perspective conventions as `docs/vault-export.md`.
    - Development layout stays in-repo; a CI job mirrors each tagged release into a dedicated distribution repo (e.g. `people-context-obsidian`) whose root carries `manifest.json` and whose releases carry `main.js`/`manifest.json`/`styles.css` — this monorepo path is never submitted to the community directory directly.
    - Docs must state that anything rendered into a synced vault leaves the project's disclosure perimeter (same caveat as vault export).
  - **Tests/validation:** `uv run ruff check .` and `uv run pytest -q` unaffected/still green (no Python changes); `vitest` unit tests over the CLI-invocation layer against recorded `--json` fixtures, including CLI-missing and non-zero-exit paths, following the `openclaw-plugin/src/index.test.ts` layout. A CI dry-run test asserting the mirrored tree contains root-level `manifest.json` plus release artifacts.
  - **Out of scope:** actual community-directory submission (a manual/one-time act after this PR, not code); write-back from Obsidian to the store (explicit non-goal); polling/interval refresh policy beyond a first default (open question 5 — ship manual/on-open first).

## M15 — Data quality, insight, and credibility

> Keep long-lived databases trustworthy and inspectable by surfacing (never auto-fixing) duplicates and contradictions, publishing a local stats report, sharpening bilingual resolution explanations, and shipping a reproducible evaluation with use-case docs.

- [ ] **PR M15.1 — `doctor` curation findings**
  - **Scope:** New narrow read port `ports/curation.py::CurationReader`, its SQLite implementation `adapters/sqlite/curation_reader.py` (finding *queries*), app-layer policy `app/run_doctor.py` (finding *codes*, ordering, message text), and the `people-context doctor` CLI command. Report-only — no write path touched.
  - **Touches:**
    - `src/people_context/ports/curation.py` (new)
    - `src/people_context/adapters/sqlite/curation_reader.py` (new)
    - `src/people_context/app/run_doctor.py` (new)
    - `src/people_context/cli.py` (`doctor` subcommand)
    - `src/people_context/adapters/sqlite/migrations/005_doctor_indexes.sql` (only if a finding query needs an additive index beyond the existing `004_curation_indexes.sql` ones — check before adding)
    - `tests/adapters/test_sqlite_curation_reader.py`, `tests/app/test_run_doctor.py` (new)
  - **Spec:** (docs/specs/m15-data-quality-and-credibility.md §"`people-context doctor`")
    - Four deterministic finding classes with stable codes: `duplicate_alias`, `duplicate_handle`, `contradictory_fact`, `dangling_reference`. `duplicate_handle` takes precedence over `duplicate_alias` — a handle-sharing pair is reported once, and `duplicate_alias` only evaluates non-`handle` alias kinds plus canonical names.
    - `contradictory_fact`: same `predicate`, overlapping `Fact.period`, differing `value` — flagged, never adjudicated.
    - `dangling_reference`: relationships/affiliations/interaction participants pointing at soft-deleted people, visible via `include_deleted` reads.
    - Never auto-applies anything — output is a report plus copy-pasteable suggested follow-ups (`show`, `merge_people`, `correct_record`, `delete`). Exit code 0 with findings; non-zero reserved for actual errors so `doctor` is cron/script-safe.
    - No LLM-based detection — findings are deterministic and explainable (shared normalized aliases, identical handles, overlapping periods), matching `resolve_person`'s staged-explanation style.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. `test_sqlite_curation_reader.py` with one fixture DB per finding class asserting detection AND non-detection (clean data → zero findings). Fake-port tests for `run_doctor` policy: stable codes, deterministic ordering, correct suggested-command rendering. CLI snapshot test over a seeded fixture including `--json` shape stability, `--only CODE[,CODE...]` filtering.
  - **Out of scope:** `stats`; an interactive apply-a-fix mode (open question 1 — stays report-only this release); any MCP tool surfacing these findings (explicit non-goal).

- [ ] **PR M15.2 — `stats` local inventory report**
  - **Scope:** CLI-only `people-context stats` report over existing reads plus small adapter count queries (no new port needed if existing readers suffice; otherwise a minimal additive method). Does not touch `doctor` or resolution.
  - **Touches:**
    - `src/people_context/app/compute_stats.py` (new, or a method alongside `run_doctor.py` if a shared adapter query module emerges — keep app-layer policy separate from `run_doctor`)
    - `src/people_context/adapters/sqlite/*.py` (small additive count queries only, e.g. in `repository.py`/`context_reader.py` — no schema change)
    - `src/people_context/cli.py` (`stats` subcommand)
    - `tests/app/test_compute_stats.py`, extend CLI test module
  - **Spec:** (docs/specs/m15-data-quality-and-credibility.md §"`people-context stats`")
    - Reports: entity counts per table, alias-kind distribution, facts/observations by `Sensitivity`, relationship-category distribution, audit-log operation counts, changelog entries per device, database size, and disclosure-gate state.
    - Disclosure-gate section reads only the local process environment (`PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE`, `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT` — see `adapters/mcp/tools/people.py`/`portability.py`) — must not probe or start the server; report as "in this environment".
    - Absolute database path is **redacted by default**; `--include-path` opts in explicitly.
    - `--json` mirrors the human output; never emits record contents, only aggregates/counts.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. Fake-port tests for the stats computation. CLI snapshot test over a seeded fixture including `--json` shape stability and the default-redacted-path / `--include-path` behavior.
  - **Out of scope:** `doctor`'s finding classes; any telemetry or network call (explicit non-goal — local-only).

- [ ] **PR M15.3 — Transliteration-aware resolution explanations**
  - **Scope:** Change `ResolvePerson`'s exact-match `match_reason` from the bare `"exact"` to a naming scheme identifying what matched, add script-pair fixture tests pinning existing rank parity, and document the bilingual workflow. No ranking change — explanation text and docs only.
  - **Touches:**
    - `src/people_context/app/resolve_person.py` (the `self._offer(best, _candidate(person, 1.0, "exact"))` call site and its canonical-name vs. alias-kind branches)
    - `docs/identity-resolution.md` (bilingual workflow section)
    - `tests/app/test_resolve_person.py` (extend)
  - **Spec:** (docs/specs/m15-data-quality-and-credibility.md §"Transliteration-aware resolution explanations")
    - New explanation strings: `"exact:canonical_name"`, `"exact:alias:native_script"`, `"exact:alias:transliteration"`, etc., replacing the bare `"exact"` for every exact-normalized-match branch.
    - This is explanation only — rank parity (exact alias match scores `1.0` like canonical-name match) is existing, untouched behavior; tests pin it as a now-tested contract, not a change.
    - Fixture-backed tests: CJK + romanization pairs and at least one non-CJK case (e.g. Cyrillic), asserting rank parity in both directions plus the new explanation text.
    - `resolve_person`'s request/response contract is otherwise unchanged — explanations are documented as descriptive text, not a parseable enum (M12 additive-contract promise).
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. Extend `test_resolve_person.py` with script-pair fixtures asserting both rank parity and explanation content for `native_script`/`transliteration` matches in both query directions.
  - **Out of scope:** fuzzy cross-script matching (open question 3 — exact normalized matches only this pass); any MCP response-shape change beyond the string content of `match_reason`.

- [ ] **PR M15.4 — Eval harness and use-case gallery**
  - **Scope:** A scripted, reproducible evaluation under `evals/` (dev-only, not shipped in the package) comparing agent task quality with/without the server attached, published as `docs/evals.md`; plus 3-5 narrative recipes under `docs/use-cases/` linked from the README. No production code changes.
  - **Touches:**
    - `evals/` (new: harness script(s), fixture seed data, task/rubric config, model-ID pins)
    - `docs/evals.md` (new, dated results + harness version)
    - `docs/use-cases/*.md` (new: job-search networking, founder investor relations, caregiving coordination, community organizing, etc.)
    - `README.md` (link use-case gallery and `docs/evals.md`)
  - **Spec:** (docs/specs/m15-data-quality-and-credibility.md §"Evaluation and use-case gallery")
    - Harness seeds a fixture DB, runs a fixed task set through an MCP-connected agent twice (with/without the server), scores identity-disambiguation accuracy against fixture ground truth and rubric-scored communication-draft quality.
    - Model IDs and prompts are pinned in config for reproducibility/re-runnability as models change.
    - Eval is a dev workflow like `openclaw-plugin/`'s tests — not part of the shipped package; any API keys stay in the runner's environment, never committed.
    - Fixture data must be fictional only; harness docs must warn against pointing it at a real personal database (privacy requirement, not just a nicety).
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green (harness code under `evals/` still lints/type-checks if it's Python, even though it's excluded from the shipped package). A dry-run-mode test executing the harness end to end against a stub agent (no network), verifying scoring plumbing without real model calls.
  - **Out of scope:** hosted/telemetry-backed benchmarking (explicit non-goal); deciding README-vs-`docs/evals.md` placement of headline numbers is a docs call, not a code dependency — default to `docs/evals.md` per open question 5 unless the user directs otherwise.
