# Moving to a new laptop

**Situation.** New machine. Two years of accumulated context lives in a SQLite file on the old one, and you would
rather not start over.

**Goal.** Carry the store across as one reviewed, verifiable step — with no server, no account, and no copy of
your data sitting on someone else's disk.

## 1. Write a bundle on the old device

```bash
pctx sync push --output ~/transfer/
```

This writes one complete bootstrap bundle — `people-context-sync-bundle.json` — containing your domain records,
audit history, and the full changelog, in a strict versioned format with deterministic ordering. The file is
written atomically and owner-only.

**The bundle is plaintext, and it is everything.** Treat it exactly as you would treat the database: move it over
a channel you trust, keep it encrypted at rest, and delete it once the restore is confirmed.

## 2. Carry it across

Any medium you already trust — an encrypted USB stick, an encrypted archive, your own file sync. There is no
relay, no pairing service, and nothing in this project that transmits the bundle for you. That is the point:
transport is your choice, so the trust boundary stays where you put it.

## 3. Restore into a fresh database on the new device

```bash
pctx sync pull --input ~/transfer/
```

`pull` refuses far more often than it accepts, and that is the feature. Before it shows you anything it validates
the bundle: wrong format or version, missing or unknown fields, duplicate ids, an invalid origin, a dangling
reference, or an insufficient watermark all fail *before* the preview and before any prompt.

It then checks the target is genuinely a fresh store — exactly one active local device, only the canonical seeded
vocabulary, and zero rows in every mutable table — reports non-sensitive counts of what it is about to import,
and asks for confirmation. It will not merge into an existing store and never clears existing state to make room.

The restore itself is one transaction: reconcile vocabulary, retire the imported devices, insert records
verbatim, rebuild the search index, advance the local clock, and either commit or roll back completely.

## 4. Confirm, then clean up

```bash
pctx list
pctx sync-log --limit 20
```

Restored history is carried over verbatim rather than re-attributed, so the changelog still shows which device
originally made each change, and your first write on the new laptop appears under its own new device id and
sorts after everything imported.

Once you are satisfied, delete the bundle from the transfer medium and from both machines.

## Scope

This is bootstrap restore into an empty database, not continuous two-way sync. Two machines that have both been
written to since diverging cannot be reconciled by this path; incremental replay between diverged devices is a
[post-roadmap candidate](../roadmap.md#post-roadmap-candidates), not a shipped feature.

## Next

- [design/sync.md](../design/sync.md) — the bundle format, device identity, and clock design.
- [Auditing what the agent can see](auditing-what-the-agent-can-see.md) — verifying the new store.
