# Your first week with a new team

**Situation.** You have joined a team of fifteen people. Names arrive faster than you can hold them, half of them
appear in three spellings, and your assistant knows none of it.

**Goal.** Get from an empty store to an agent that can answer "who is Priya again?" by the end of the week —
without pasting your inbox into a cloud service.

## 1. Seed yourself first

```bash
pctx init
```

`init` asks for your canonical name, your email handles, and an optional one-line communication philosophy. It
records *you* first for a reason: knowing which identity is you is what lets every later import skip your own
contact card and leave you out of the participant list on your own meetings.

It refuses to run destructively. On a store that already has people, it will only proceed when exactly one
active self identity exists and you confirm it, so running `init` twice cannot quietly create a second you.

## 2. Bring in what you already have

Ask your agent to import an export you already own — a vCard file from your address book, a calendar `.ics`, a
LinkedIn connections CSV, an mbox, or an Outlook or WhatsApp export:

> Import the contacts in `~/Downloads/team.vcf`, then show me what you found before saving anything.

The agent calls `import_content`, which distils candidates and stages them. Nothing is committed yet. It then
calls `review_import` so you can see the proposals, and only commits the ones you accept with `commit_import`.

Two properties matter here:

- **Raw source content is never stored.** The importer keeps distilled candidates — names, handles, affiliations,
  a neutral summary — and discards the message or file body. A WhatsApp export's message text never reaches the
  database, the logs, or an error message.
- **Nothing is auto-committed.** A staged batch sits there until you accept specific candidates.

See [import.md](../import.md) for the seven supported sources and exactly what each one extracts.

## 3. Record the things that are not in any export

The useful half of knowing a colleague is never in a vCard. Just say it:

> Kofi Mensah is a platform engineer at Kestrel Analytics. He owns the reporting pipeline and prefers to be
> asked in writing rather than pulled into a call.

The agent resolves the person, then records an affiliation, a fact, and an observation. Facts are objective and
time-aware; observations are explicitly subjective. Keeping them apart is what stops "I think he prefers
writing" from hardening into "he prefers writing" a year later.

## 4. Check what you have

```bash
pctx list
pctx show "Kofi Mensah"
```

By Friday, `pctx list` is a roster of everyone you have met, and `pctx show` gives one person's full record —
aliases, affiliations, facts, relationships, and recent interactions.

## What stays local

Everything. The store is a SQLite file on your disk, the importer runs in your process, and no ordinary command
touches the network. If your agent runs in the cloud, it sees only what a specific tool call returns — bounded,
sensitivity-filtered context, not the database.

## Next

- [Ten minutes before a meeting](before-a-meeting.md) — using what you just recorded.
- [Auditing what the agent can see](auditing-what-the-agent-can-see.md) — checking the disclosure boundary.
