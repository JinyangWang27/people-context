# Evaluation harness and recorded results

This page documents how `people-context` is measured, what has actually been measured so far, and what a number
produced by the harness may and may not be used to claim.

Harness version: **1.0.0**. Suite: **`people-context-core` v1.0.0**. World fixture: **`tidepool-2026-08`**.
Source: [`evals/`](../evals/README.md).

## What the harness measures

One question, asked the same way every time: does an agent that can read a people-context store answer questions
about the people in it better than the same agent without one?

Each task is run twice under conditions that differ in exactly one respect.

| Condition | What the agent has |
| --- | --- |
| `with_mcp` | The same prompt, plus a people-context MCP server bound to the fictional store |
| `without_mcp` | The same prompt and nothing else |

The model, the system prompt, the task prompt, and the rubric are identical across the two. The harness passes
`--strict-mcp-config` in the base argument vector precisely so the `without_mcp` condition cannot silently pick
up a people-context server from the operator's own user or project MCP configuration; without that flag the two
conditions would not be comparable on a developer machine.

## The fictional world

[`evals/suite/world.json`](../evals/suite/world.json) is the whole world, readable in one sitting: six invented
people around a coastal restoration partnership, their organisations and roles, six facts, five interactions, and
four relationship edges, frozen at `2026-08-01T09:00:00Z`. Every address uses the reserved `.test` domain.

It is deliberately built so that generic knowledge cannot answer the tasks: two contacts share the first name
Priya and differ only in surname, employer, and role; the shortest relationship path runs through two
intermediaries; and the most overdue contact is not the most recently mentioned one.

The harness materializes the fixture through the ordinary audited use cases with the clock frozen at the
fixture's `as_of`, so the evaluated store is the same shape a real one would be and two builds of the same
fixture record the same timestamps.

## The tasks

| Task | What it probes | Possible weight |
| --- | --- | ---: |
| `identity-disambiguation` | Picking the right person out of two who share a first name | 6 |
| `context-recall` | Reporting a contact's organisation, role, and stored update preference | 7 |
| `guided-drafting` | Drafting to the user's stated philosophy and the recipient's stated preference | 9 |
| `relationship-path` | Naming the people on the shortest path to a contact, in order | 6 |
| `stale-follow-up` | Naming the most overdue contact and the date of the last interaction | 6 |
| **Total** | | **34** |

Where a task cites stored guidance, every clause of that guidance is scored. The drafting task carries four
criteria for four clauses: the philosophy's "name the decision" and "name the date", and the recipient's "bullet
points" and "no preamble". A draft that honours three of them earns three.

The exact prompts live in [`evals/suite/suite.json`](../evals/suite/suite.json) and are also copied verbatim into
every report, so a published result always carries the wording that produced it.

## How answers are scored

Scoring is textual and rule-based. No model judges another model, so the same transcript scores identically on
every machine and a reader can re-derive a published number from the recorded answer.

| Criterion kind | Passes when |
| --- | --- |
| `answer_contains_all` | Every listed phrase appears in the answer |
| `answer_contains_none` | None of the listed phrases appears |
| `answer_matches` | A case-insensitive regular expression matches |
| `answer_lines_match` | At least `min_lines` individual lines match an expression |

Rubrics also reject non-answers. Declining to answer while naming every right string — "I cannot confirm whether
Priya Raman is the Data Lead at Kestrel Analytics" — is not a correct attribution, and background prose before a
bulleted message violates the second half of a preference that reads "Bullet points, no preamble".

Answers are compared after Unicode NFKC composition, case folding, and whitespace collapsing, so line wrapping
never changes a score. Word-boundary patterns are what separate `Priya Raman` from `Priya Ramanathan`, which
plain substring matching cannot do, and ordered patterns are what stop a reversed relationship path from scoring
as a correct one.

Where a task's prompt asks the agent to use stored context, a criterion measures that specifically. The drafting
task scores bullet formatting because bullet points are the *recipient's* stored preference, so an otherwise
well-written single-line message cannot earn full marks for context the agent never read. That criterion is the
reason `answer_lines_match` exists: it is the one kind that keeps line boundaries, because collapsing them would
read any single-line message with two dashes in it as a bulleted list.

Each report records the exact operands — the phrase list or the regular expression — beside every criterion
outcome, so an older published result stays re-derivable after the suite has moved on and `suite.json` no longer
contains that rule.

Each criterion carries a weight; a task's score is the earned share of its possible weight. Partial credit is
intentional — an answer that names the right person but omits their stated update preference is better than one
that does neither, and the report shows which criterion failed.

## Running it

### Offline dry run — no key, no network

```bash
uv run python -m evals.harness --runner stub
```

The `stub` runner replays the hand-written answers in
[`evals/suite/stub-transcripts.json`](../evals/suite/stub-transcripts.json). It starts no process and opens no
socket. Run it after changing a task, a rubric, or the fixture.

### Model-backed run

```bash
export ANTHROPIC_API_KEY=...
uv run python -m evals.harness --runner claude-cli --out evals/results/<date>-<model>.json
```

The evaluated server is pinned twice. It runs from this checkout rather than resolving `people-context` from
PyPI by bare name, so a later release cannot answer the same suite differently; and it runs with its clock frozen
at the fixture's `as_of` via `python -m evals.harness.server`, so time-dependent reads such as
`get_stale_relationships` report the same "days since" whenever the run happens. That wrapper is the shipped
server — same `build_server`, same tools, same stdio transport — with only the clock injected. The report records
the configured vector so a reader knows which code was measured.

Each invocation is isolated twice over. The agent process runs in a fresh empty directory that is never under
`--workdir`, so a command-backed agent cannot read `world.db` straight off disk during the `without_mcp` control,
and cannot leave session state where the control run that follows would find it. Under `with_mcp` it also gets its
own byte-identical copy of the fictional store: the server exposes write and destructive tools, and a mutation
made while answering one task must not change what later tasks are scored against. The pristine store is never
handed to an agent.

If the agent command exceeds its output cap or its deadline, the run is refused rather than scored on truncated
output — an answer the harness had to cut is not an answer worth publishing.

The report records the agent client's own version when the suite configures a `version_argv` probe, because the
same model through a different CLI build can see different built-in prompts and MCP handling. The probe is
best-effort: if it fails, the report records nothing rather than failing the run.

The API key is read only from the process environment, and only because the suite names `ANTHROPIC_API_KEY` in
`env_passthrough`. It is never accepted as a flag, never read from a file, and never written into a report. The
suite refuses an `env_passthrough` entry beginning with `PEOPLE_CONTEXT`, so store configuration cannot reach the
agent process; the fictional database is named on the MCP server command line instead.

Before recording a model-backed result, check the `claude-cli` argument vector in `suite.json` against your
installed CLI's `--help`. The vector is a starting point, not a verified invocation: agent CLIs rename flags
between versions, and a wrong flag would produce a confident but meaningless number.

## Recorded results

### 2026-08-15 — offline dry run (plumbing only)

Report: [`evals/results/2026-08-15-stub-dry-run.json`](../evals/results/2026-08-15-stub-dry-run.json).
Harness 1.0.0, suite `people-context-core` v1.0.0, runner `stub`, model id `stub/recorded-answers`.

| Condition | Tasks | Earned | Possible | Percent |
| --- | ---: | ---: | ---: | ---: |
| `with_mcp` | 5 | 32 | 34 | 94.1 |
| `without_mcp` | 5 | 8 | 34 | 23.5 |

**This is not a measurement of any model.** The answers are hand-written illustrations chosen to exercise every
scoring path, including partial credit in both conditions. The run establishes only that the fixture
materializes, the prompts load, the rubrics discriminate, and the report is well formed.

### Model-backed runs

**None recorded yet.** No result in this repository was produced by a language model. When a model-backed run is
recorded, it will appear here as its own dated section naming the model id, the harness and suite versions, and
the report file, alongside the dry run rather than replacing it.

## Known limits

### What the rubrics do not score

The drafting task scores three of the four clauses it cites: the philosophy's "name the decision" and "name the
date", and the recipient's "bullet points". It does **not** score "no preamble" beyond rejecting a fixed list of
canned pleasantries.

That is deliberate, and it is a retreat. Four formulations were tried and each was wrong in one direction or the
other: requiring the recipient's name first rejected valid bullet-first messages; allowing any leading bullet
accepted a bulleted preamble; matching the opening line's subject accepted a preamble that mentioned the
recipient; and anchoring that match would again reject an opener like `- Confirm the September operations
review`. Telling background prose from the message itself is a semantic judgement, and this harness does not make
those — no model judges another model here. A criterion that systematically penalises a class of correct answers
would bias the measurement, so the clause is left unscored and said so out loud. Under-measuring compliance is the
safer error for a harness whose purpose is credible numbers.

### What is not reproducible

Two things are pinned: the server code, and the server's clock. One thing is not.

Materializing the fixture goes through the ordinary write use cases, which mint fresh ULIDs, so the person, alias,
record, audit, and device identifiers differ between two builds of the same world. Those ids appear in tool
responses such as `resolve_person` and `get_person_context`, which means two runs of the same suite show the model
textually different transcripts.

This is a real difference and it is left in place deliberately. Seeding stable identifiers would mean threading an
id generator through the application's write use cases — a change to shipped core code for an evaluation-only
benefit — and no rubric matches an identifier, so no score depends on one. What a reader should take from this:
the *content* of the evaluated store is fixed and re-derivable from `world.json`, while its opaque identifiers are
not, and a claim of bit-for-bit input equality between two runs would be false.

## What a number from this harness may claim

- It may claim that, on these five fixed tasks over this fictional world, a given model scored *X* with the
  server and *Y* without it, under the recorded harness and suite versions and against the pinned checkout.
- It may not be generalized to a real store. Five tasks over six invented people are a sanity check, not a
  benchmark, and a real store is larger, messier, and differently distributed.
- It may not be compared across harness or suite versions. Bump both when prompts, rubrics, or the fixture
  change, and re-record rather than editing an old result.
- It is not a claim about a competing product. The comparison here is one agent with and without this server;
  the dated local-versus-cloud comparison lives in
  [privacy-and-safety.md](privacy-and-safety.md#threat-model-notes).

## Privacy

- The harness builds its own SQLite store in a throwaway directory. It refuses to run against the database the
  local configuration resolves to, and refuses to open any database file that already exists, so a wrong
  `--workdir` cannot read, migrate, or overwrite personal data.
- No real personal data is copied into fixtures, prompts, results, or this page. Everything in the world fixture
  is invented.
- Report documents are ordinary local files written owner-only. They contain the fictional answers verbatim;
  inspect a report before attaching it to an issue, the same as any other local export.
- Running the harness never contacts the network unless you select a model-backed runner, which reaches only the
  agent CLI you configured.

## Related reading

- [Use-case gallery](use-cases/README.md) — narrative recipes for the workflows the tasks abstract.
- [compatibility.md](compatibility.md) — the additive promise the report document follows.
- [privacy-and-safety.md](privacy-and-safety.md) — disclosure gates, audit, and the threat model.
