# Auditing what the agent can see

**Situation.** You have been recording notes about real people for months. Before you let an agent near it again,
you want to answer three questions honestly: how much is in there, how much of it is wrong, and what exactly can
a model read?

**Goal.** Inspect the store's footprint, its quality, and its disclosure boundary — without any of those checks
becoming a new disclosure.

## 1. How much is in there

```bash
pctx stats
pctx stats --json
```

`stats` reports aggregates and nothing else: entity counts, alias-kind distribution, facts and observations by
sensitivity, relationship categories, audit operations, changelog counts by opaque device id, and storage bytes.

The adapter that produces it cannot return record text, device display names, or absolute paths — it is built to
count, not to read. Storage is the main database plus its `-wal` and `-shm` companions, because WAL mode would
otherwise make the number materially wrong. For an in-memory database it reports an explicit null state rather
than a misleading zero.

Your database path is **redacted by default**; `--include-path` opts in. Note that aggregates can still be
sensitive — "eleven restricted facts" says something — so treat `stats --json` output as personal metadata when
deciding whether to attach it to a bug report.

## 2. How much of it is wrong

```bash
pctx doctor
pctx doctor --only duplicate_handle,contradictory_fact
```

`doctor` finds four classes of problem and repairs none of them: two active people sharing a handle, two sharing
name material, contradictory facts with overlapping validity periods, and references pointing at soft-deleted
people. It exits zero when it completes, whether or not it found anything — findings are a report, not an error.

Every finding carries a **structured** suggested action rather than an interpolated shell string: either a CLI
argument vector such as `["pctx", "show", "<person-id>"]`, or an MCP tool name with id arguments for
`merge_people` or `correct_record`. Actions reference ids, never names, and nothing executes. You read the
finding, you decide, you run the repair.

There is deliberately no doctor MCP tool. Repair stays explicit and human-approved.

## 3. What a model can actually read

Two capabilities are gated behind environment variables read from the **server's own process**, never from tool
arguments:

| Variable | Unlocks |
| --- | --- |
| `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` | `get_sensitive_person_context` |
| `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT` | `export_data` |

Unset, those tools are not registered at all — an agent cannot call what does not exist in its tool list, and no
amount of persuasion in a prompt changes a variable in the process that started the server. `pctx stats` reports
the state of both gates as they are *in the CLI's environment*, described as "in this environment", so you can
see what a server started the same way would expose.

Ordinary `get_person_context` withholds sensitive and restricted records and labels what it disclosed.

## 4. What has happened to the data

```bash
pctx sync-log --limit 50
pctx sync-log --entity <id> --payloads
```

Every ordinary durable mutation flows through one audited seam, so the changelog is a complete record of what
changed and when. `--payloads` is off by default because replay payloads may contain sensitive values; ask for
them deliberately.

## 5. Removing something for real

> Forget everything about that contact.

`forget` hard-deletes the person graph or a single record and redacts identifying audit and changelog history,
leaving a `{"redacted": true}` tombstone. It is a real deletion of the row, not a hidden flag — which is a thing
you can verify yourself, on your own disk, with the sqlite3 CLI.

## What you should see

A store you can describe in one paragraph: how many people, how much is sensitive, how big it is, which of the
two elevated tools are off, and a doctor report short enough to read.

## Next

- [privacy-and-safety.md](../privacy-and-safety.md) — the full disclosure model and threat notes.
- [compatibility.md](../compatibility.md) — what stays stable in the JSON these commands print.
