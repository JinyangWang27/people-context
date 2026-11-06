# Documents and shared contexts

Read this before capturing from a CV, biography, or page of notes, capturing a shared school, work, club, or household context, or answering how two people know each other. Read `staged-capture.md` first.

### Capturing a CV, biography, or page of notes

A CV, a bio page, or a folder of background notes runs through the same staged flow with one extra
discipline: **the document asserts, it does not establish.** Every line in it is a claim by whoever
wrote it, and storing a claim must never quietly promote it to a verified characteristic.

1. **Read it yourself.** people-context has no parser and keeps no copy — it never sees the document.
   Say plainly when a file is unreadable or only partly readable, extract only what you actually read,
   and never present a partial read as a complete CV.
2. **Resolve the subject first** with `resolve_person`, along with anyone else the document names. An
   ambiguous match stays unresolved: do not turn it into a new person, and do not attach a CV to
   whichever candidate looked likeliest. Do not lean on staging to catch this for you either. A batch
   of `person`, `affiliation`, and `fact` candidates is the released pre-M17 shape, and its matcher
   reports no ambiguity at all — a unique handle binds the claim even when the name on the document
   matches two other people, and a name matching two with nothing to separate them fails the whole
   commit after the user has already reviewed it. `pctx import stage-candidates` always uses the
   ambiguity-preserving matcher; `stage_candidates` does so only for a batch that also carries an
   `observation`, `trait`, or `relationship`.
3. **Pick the record type from what the document supports.** Employment and education are `affiliation`
   candidates *when* role, organisation, dates, and disclosure can all be represented faithfully.
   Qualifications, skills, languages, and other background are `fact` candidates. `observation` and
   `trait` keep the meanings they have in `staged-capture.md` — a CV is not a meeting you sat in.
4. **Attribute every claim** with `stated_by`. "Describes herself as analytical in her CV" is a `fact`
   whose value says exactly that and whose `stated_by` names her. It never becomes a `trait` valued
   "analytical", because nobody observed it. Attribution is not verification, and a confident-sounding
   document is no reason for a high `confidence`.
5. **Stage with a receipt when you have one.** A `source_kind` plus a `content_digest` over the bytes
   you actually read records that this artifact was processed. That is all it records: a receipt is
   evidence of processing, never verification of the claims inside it. A `label` is caller metadata —
   no private source content, no filesystem path.
6. **Review, commit, read back.** Show the review with its uncertainty and its reading limitations,
   commit only the candidates the user explicitly accepted, then re-read the person and report what
   committed and what stayed unresolved.

#### Only the dates the source actually supports

`valid_from` and `valid_to` take the exact dates the document gives you and nothing else. Year-only,
month-only, approximate, and unknown periods stay in the claim text, where their uncertainty survives:
"CV reports study at Northbridge College, 2017–2019; months and days unknown." Never invent a January 1,
a month boundary, or a date taken from when you happened to read the file — a recording date is not an
event date.

This matters most at the open end. An affiliation with no `valid_to` reads as current everywhere, so a
role you know only as historical must not be staged as one. Capture it as an attributed background fact
carrying its own uncertainty, or leave it unresolved and say why. A document's silence about an end date
is not a claim that the role continues. An explicit "current" in the source is such a claim, and can be
staged as one with its attribution and whatever bounds are known.

Two roles at once are ordinary — a job and a board seat, a lecturer and a consultant — and are not a
contradiction to resolve. Nothing a CV leaves out closes or deletes anything: omission is not an ending.
A second, revised, or contradictory CV is more evidence, not permission to overwrite. That is a
maintenance pass under the rules in `maintenance.md`, never a silent rewrite during extraction, and a claim repeated
across three documents is not thereby more likely to be true.

#### Background that needs protecting

Affiliations and relationships carry no sensitivity field, so they cannot protect anything. Health,
immigration, financial, and comparable background belongs in a `fact` at `sensitive` or `restricted`,
and must not leak sideways into a record that cannot hold it: not into the organisation name, not into
a graph edge, not into a person `summary`, and not into a `stated_by` string.

#### Two worked examples

A readable CV whose subject resolves to exactly one person:

```json
[
  {"type": "person", "ref": "nadia", "name": "Nadia Okonkwo",
   "aliases": [{"value": "nadia.okonkwo@example.com", "kind": "handle"}]},
  {"type": "affiliation", "person_ref": "nadia", "org": "Northbridge Analytics",
   "role": "Senior Data Engineer", "valid_from": "2023-04-03",
   "stated_by": "Nadia Okonkwo (CV)", "confidence": 0.7},
  {"type": "fact", "person_ref": "nadia", "predicate": "skill",
   "value": "CV lists Rust and distributed systems as primary skills",
   "stated_by": "Nadia Okonkwo (CV)", "confidence": 0.6},
  {"type": "fact", "person_ref": "nadia", "predicate": "education",
   "value": "CV reports study at Northbridge College, 2017–2019; months and days unknown",
   "stated_by": "Nadia Okonkwo (CV)", "confidence": 0.6}
]
```

The dated role is an affiliation because the CV gives a start date and says it is current. The degree is
a fact because 2017–2019 is not a pair of dates, and forcing it into an affiliation would either invent
two days or open a period that reads as a job she still holds.

The same document when its middle pages are an unreadable scan and the name matches two active people:
stage nothing. Report what you have — "the employment section did not come out, and 'J. Okonkwo' matches
two people in the store; tell me which one and I will bring you the rest." A guessed subject or an
invented date is worse than a gap, because afterwards the store cannot tell which values were read and
which were filled in.

Neither pass claims to be complete. A capture records what one document asserted and one person accepted;
it is not a survey of what is knowable about someone, and nothing here notices that a newly staged claim
means the same thing as one already stored.

### Capturing a shared context: school, work, club, household

"We were in the same class", "she was on my team at Globex", "they are in my running
club" describes a **group**, and a group is what later answers how two people know each
other. Two candidates record it: a `group` for the context, and one `group_membership` per
person placed in it. Both go through the same stage → review → commit flow as everything
else; nothing here is a direct write.

Four rules, and they are the whole difficulty:

- **Name the material with `source_kind`.** A batch containing a `group_membership` is
  refused without it. The receipt is what lets a group committed in one call be named by a
  membership committed in the next; without it the membership could only be reattached by
  matching the group's name, which is exactly what the next rule forbids.
- **Never guess a group.** Staging looks nothing up by name, so committing a `group`
  candidate creates a *new* group unless you pass `group_id` — and there is no merge to
  undo a wrong one. If the user may already have the group stored, call `find_groups`
  first, show what came back, and pass the id they confirm. Two groups called "Class 1"
  are different rooms until the user says otherwise.
- **Never fill in a date.** Record `valid_from` and `valid_to` only where the source
  gave them, and never a start later than the end — that is refused, as it is on a `fact`
  and an `affiliation`. Absent means unknown, not "still going": `temporal_basis` is then `unknown`,
  and unknown timing supports a shared context and never a `classmates` or `teammates`
  label. Set `ongoing` only when the user actually said it is current.
- **Never extrapolate a roster or a year.** "We stayed together through Grade 9" records
  the people the user named and the extent they confirmed — one membership each, one
  known span. It is not a placement per grade, not a class number, not an assumption the
  rest of the class came along. Progression from a known year to a later one is a
  question to ask, never a record to write.
- **A label with no group is still a relationship.** "My classmate Dana", with no
  identified class, is a `relationship` candidate. Confirm which class it was before a
  group exists at all.

Placement is context, not employment: `organization_id` says a team sits under an
organization and asserts nothing about who anyone works for. Default `sensitivity` is
`personal`; raise it on the group, the membership, or both when the user would not want
it in an ordinary read.

### Answering "how do they know each other"

`explain_shared_connections` is the tool for shared-background, introduction, and
connection questions — "how do Alice and Bob know each other", "do they already know each
other", "what do we have in common". Call it when the question is about a pair, without
waiting for the user to name a tool, and resolve both people first as always.

Read what it returns exactly as it is written, and say the difference out loud:

- `shared_context` means both people hold a membership in one group. That is all it
  means. It is not evidence they know each other, are friends, or ever met.
- `derived_relation` with a `label` of `classmates` or `teammates` additionally required
  compatible roles and an established common time. A teacher and a pupil of one class are
  not classmates.
- `temporal` says what the dates establish: `overlap` proves a common day, `disjoint`
  excludes one, and `unknown` means the recorded dates prove neither. Report `unknown` as
  unknown rather than as "probably".
- `direct_relationships` are assertions somebody recorded. Keep them distinct from
  anything derived — they are a different kind of claim.

Two different negatives, and they must not be reported the same way. An **empty
`connections` list with `found: true`** is the scoped negative: both people were read, and
no shared group was found among the records you may see. It is not proof that they are
unrelated or have never met, and must never be reported as such. **`found: false`** is not
a result about the pair at all — one of the two could not be read, because the id is
unknown or the person was forgotten or merged away. Resolve the people again rather than
telling the user there is no connection.

The lookup is explicit and stays explicit. Do not add derived classmates or colleagues to
ordinary `get_person_context` results, to graph reads, or to a meeting brief: those tools
keep the meanings they already have. Nothing here writes anything.
