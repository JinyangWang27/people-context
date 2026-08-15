# Ten minutes before a meeting

**Situation.** A calendar reminder fires: *Restoration reporting sync — Priya*. You know two people called Priya
and cannot remember which one owns the metrics.

**Goal.** Walk in knowing who you are meeting, what you last agreed, and how they like to be talked to.

## 1. Ask by the name you actually remember

> Who is the Priya I'm meeting about reporting?

The agent calls `resolve_person` rather than guessing. Resolution is explainable: it returns ranked candidates
with a score and a match reason, and marks the result `ambiguous` instead of silently picking one. Two people who
share a first name come back as two candidates, each with its person id, canonical name, aliases, and whatever
one-line summary you stored.

A candidate carries identity, not a dossier: there are no affiliation or role fields on it. Telling the two
Priyas apart by employer means reading one of them, which is the next step — so expect the agent to resolve
first and then look up, not to answer from the candidate list alone.

Since M15.3, an exact match also carries an optional `match_detail` saying *which stored name* matched — the
canonical name, or an alias and its kind. When a colleague is stored under both a native-script name and a
romanization, that is the difference between "it matched" and "it matched your transliteration".

## 2. Get the bounded context

> Give me the context for Priya Raman.

`get_person_context` returns a bounded, sensitivity-aware bundle: affiliations, recent ordinary interactions,
facts, and active reminders. Records marked sensitive or restricted are withheld unless the operator has
explicitly enabled the elevated tool in the server's own environment — a prompt cannot talk its way past that
gate, because the gate is not read from tool arguments.

## 3. Ask how to talk to them, not what to say

> How should I approach the reporting conversation with her?

`get_communication_guidance` returns *signals*, not generated advice: traits, relationships, roles, friction
notes, active reminders, and your own recorded communication philosophy. The model does the writing; the store
supplies the evidence. That split is deliberate — it keeps the stored record auditable and leaves the judgement
where you can see it.

## 4. Or skip the agent entirely

```bash
pctx brief "Priya Raman"
```

`brief` composes the same material into a deterministic Markdown brief you can read yourself. Two flags matter:

- `--json` prints the versioned brief document instead, stable for other tools to consume;
- `--output FILE` writes it to an owner-only file rather than your terminal scrollback.

Sensitive records stay out unless you pass `--include-sensitive`, which widens the context records only —
communication guidance stays ordinary either way, and the brief labels what it disclosed.

## What you should see

An illustrative brief opens with identity and affiliation, then the last few interactions with dates, then facts
and reminders. If a section is empty it says so rather than inventing filler.

## Next

- [Staying in touch on purpose](staying-in-touch.md) — noticing the meetings that never got scheduled.
- [communication-guidance.md](../communication-guidance.md) — what guidance does and does not return.
