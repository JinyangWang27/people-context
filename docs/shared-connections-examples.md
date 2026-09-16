# Shared connections examples

Eight worked scenarios for the group capture and shared-connection guidance delivered in
[`skills/people-context-usage/SKILL.md`](../skills/people-context-usage/SKILL.md) and mirrored into the packaged
guidance served as `people-context://guide`. They exist so a human can judge whether an agent captures the shared
context a conversation actually supports and reports a lookup honestly — which is not something this repository's
automated checks can decide. See [Human review of shared-context capture](evals.md#human-review-of-shared-context-capture)
for the rubric that goes with these cases.

**Everything here is invented.** The people, the school, the employers, the clubs, and the conversations are
fiction written for this document. No real person, roster, or correspondence was used, quoted, or paraphrased.

**A group is a context, not proof that its members know one another.** That is the line every scenario below is
written around. Two people recorded in one class shared a room; whether they ever spoke is not something the store
knows, and no lookup here will say it does.

**Missing dates stay missing.** `valid_from` and `valid_to` are recorded only where a source gave them. An absent
bound is unknown, never open-ended, and unknown timing supports a shared context and never a `classmates` or
`teammates` label. Several scenarios below end with a weaker result than the conversation felt like it supported;
that is the workflow working.

**No roster extrapolation.** A confirmation about three people covers three people. "We stayed together through
Grade 9" records the extent that was confirmed and never a placement per grade, a class number nobody gave, or an
assumption about who else came along.

**The replies are illustrative.** They show the shape of a capture or a report that satisfies the guidance — the
group resolved before anything is staged, the result read back as written — not a promise about wording. A
different model, or the same model on a different day, will phrase these differently and still be right.

Tool names in `monospace` such as `explain_shared_connections` are MCP tools the agent calls on the user's behalf;
see [mcp-interface.md](mcp-interface.md) for the inventory. Where a name appears in a non-Latin script it is given
natively with a romanization on first use, following [identity-resolution.md](identity-resolution.md).

## 1. A class with a term both people sat in (English)

**Situation.** The user is putting together an introduction and wants to know what two people already share.

> Amara Okonkwo and Priya Raghunathan were both in my sixth-year class at Ridgemont — September 2015 through
> June 2016. Can you record that?

The agent resolves both people, then checks whether the class already exists rather than assuming:

> Before I record it — do you already have that class stored? Let me look.

`find_groups` for "Ridgemont" returns nothing. The agent says so and stages a new group rather than reusing a
near-match it did not find:

> Nothing stored yet, so I'll create the class as a new group and place both of them in it for that term.

**Staged.** One `group` — `name: "Class 6B, Ridgemont"`, `kind: class` — and two `membership` candidates,
`role: student`, `valid_from: 2015-09-01`, `valid_to: 2016-06-30`. The user reviews the batch and commits it.

Asked afterwards how the two are connected, `explain_shared_connections` returns a `derived_relation` labelled
`classmates` with `temporal: overlap`, because the recorded terms prove a common day and both people held the
student role.

**Not done.** No relationship edge between Amara and Priya. The class is the evidence; a `relationship` would be a
separate assertion nobody made.

**Lesson.** This is the only shape that earns the narrower label: one identified group, compatible roles, and
dates that prove a shared day. Everything else in this document falls short of one of the three.

## 2. The same school, different classes (English)

**Situation.** The user remembers both people from school but not from the same room.

> Amara and Tomas Bergström were both at Ridgemont. Different classes though — he was a year below.

The agent does not reach for the class it already has:

> Then I'll record the school itself rather than putting him in 6B. Which years was he there?

> No idea, honestly.

> I'll leave the dates off, then.

**Staged.** A second `group` — `name: "Ridgemont School"`, `kind: community` — and a `membership` for each of
them with no dates, so `temporal_basis` is `unknown`.

The lookup afterwards reports one `shared_context` in Ridgemont School with `temporal: unknown`, and the agent
reports it as it stands:

> They were both at Ridgemont. Nothing recorded says they were there at the same time, and different classes means
> the school is all they're recorded as sharing.

**Not done.** No classmate label, no "probably overlapped", and no membership in 6B for Tomas. The school is a
group; the class is a different group; being in one says nothing about the other.

**Lesson.** The default for a group without a supported rule is an explained shared context. Reaching for the
stronger word because the situation feels like it would be right is the failure this scenario exists to catch.

## 3. A class with no dates at all (Chinese)

**Situation.** 林书宁 (Lin Shuning) and 高维安 (Gao Wei'an) went to school together, and the user cannot say when.

> 林书宁和高维安是初中同班同学，具体哪几年我真记不清了。

> I can record the class and place them both in it, but I won't record a start or end year — I'd be inventing
> them, and a guessed year would make later answers look more certain than they are.

> 那就别写年份。

**Staged.** One `group`, `kind: class`, and two `membership` candidates with `role: student` and no dates.

Asked later how they know each other, the lookup returns `shared_context` with `temporal: unknown` — not
`classmates`, even though both hold the student role in one class, because nothing establishes a common day.

> They're both recorded in the same middle-school class. The dates aren't recorded, so I can't say from what's
> stored that they were there at the same time — only that the class is the same one.

**Not done.** No invented school years, and no `classmates` label from the role alone. The role rule and the time
rule are separate requirements and both have to hold.

**Lesson.** "Same class" and "classmates at the same time" are different claims, and the store keeps them apart
even when the user's own sentence ran them together.

## 4. Known dates that do not touch (English)

**Situation.** Two people at one employer, years apart.

> Dana Okafor was at Globex from 2018 to 2020, and Marcus Hale started there in 2022.

The agent records both and then reads the result back without softening it:

> They were both at Globex, but not at the same time — Dana left in 2020 and Marcus arrived in 2022. If you want
> an introduction, the shared ground is the place, not the people.

**Staged.** A `group` for the Globex engineering team, `kind: team`, and two `membership` candidates with the
dates as given.

The lookup returns one connection with `temporal: disjoint` and no label.

**Not done.** The agent does not suggest they might know each other through people who stayed, and stages no edge
to anyone who did. A common person is not shared group evidence.

**Lesson.** `disjoint` is a real answer and a useful one. Reporting it plainly is more helpful than hedging it
into something that sounds like a connection.

## 5. A teacher and a pupil (English)

**Situation.** The user adds the person who taught the class from scenario 1.

> Mr Adeyemi taught 6B that year. Add him to the class.

The agent records him in the class and says what changes and what does not:

> Recorded — same class, same year, `role: teacher`. That won't make him and Amara classmates, though. The label
> needs both people in the same role, and a teacher and a pupil sharing a room aren't peers.

**Staged.** One `membership`, `role: teacher`, with the same term dates as the students.

The lookup between him and Amara returns `shared_context` with `temporal: overlap` — the dates do prove a common
day — and no label, because the roles differ.

**Not done.** No `classmates`, and no relationship edge asserting he taught her. That would be a claim about the
two of them, which the membership does not make.

**Lesson.** Overlap alone is not the label. Role compatibility and established time are both required, and this is
the case where one holds and the other does not.

## 6. A cross-company project team (English)

**Situation.** A team with people from two employers.

> The Northwind migration team was me, Dana from Globex, and two contractors from Halden Systems.

The agent records the team and then refuses the inference the placement invites:

> I'll record the project team with its members. I'll place it under Northwind for context, but that won't say
> any of them work for Northwind — the contractors' employers are separate records, and I'd want you to confirm
> those separately if you want them stored.

**Staged.** A `group`, `kind: team`, `organization_id` naming the existing Northwind organization, and one
`membership` per participant with `role: participant`.

The lookup between Dana and a contractor returns a `derived_relation` labelled `teammates` where the recorded
dates overlap — participants in one team is exactly the supported case — and `shared_context` where they do not.

**Not done.** No affiliation to Northwind for anyone. Organizational placement is context and creates no
employment, no department membership, and no person-to-person tie.

**Lesson.** Placement under an organization and membership in it are different records, and conflating them would
put a contractor on a payroll they were never on.

## 7. Confirmed continuity, without inventing the years (Chinese)

**Situation.** The user confirms a cohort stayed together, and the agent's job is to record exactly that.

> 我、林书宁和高维安从初一一直同班到初三毕业，2015到2018年。

> That's the three of you, and 2015 through 2018. I'll record it as one continuing cohort with that span — not as
> three separate class placements, because you've told me you stayed together, not which class number each year
> was.

> 对，就是这样。

**Staged.** One `group`, `kind: cohort`, and three `membership` candidates with `role: student`,
`valid_from: 2015`-dated and `valid_to: 2018`-dated as the user gave them.

Then the agent asks rather than extends:

> Did the same group carry on into senior school, or does it stop at 2018?

> 高中就分开了。

> Then it stops there. I won't record anything past 2018.

**Not done.** No class number for any year, no membership for anyone the user did not name, no per-grade
placement, and no year added after 2018. Three people confirmed is three people recorded.

**Lesson.** Confirmed continuity is one assertion with a known extent, not a generator for a row per academic
year. The question about senior school is fine to ask; persisting the answer before it arrives is not.

## 8. Two friends of one person (English)

**Situation.** The classic transitive trap.

> Amara and Marcus both know me pretty well. Do they know each other?

The agent calls the lookup and reports what comes back:

> Nothing stored says they do. They're each connected to you, but that's two separate relationships, not a shared
> group — and I've got no class, team, or club with both of them in it.

> Could you note them as acquaintances?

> I can record that if you want it, but it would be your assertion rather than something the stored records
> support. Do you actually know they've met?

> Now that you ask — no.

> Then I'll leave it.

**Not done.** No group invented to hold them both, no `acquaintance` edge, and no membership derived from the two
relationships through the user. A negative lookup is recorded nowhere: it is the absence of evidence, not a stored
finding that the two are strangers.

**Lesson.** `found: false` means no supporting shared context was found in what the agent may see. Reporting it as
"they don't know each other" would state something the store never checked.

## What the checks cover, and what they do not

The automated tests in this repository cover the candidate lifecycle: that group and membership candidates stage
with their references rewritten, that a batch whose group reference cannot be resolved is refused whole, that a
membership commits only after its group does, that unknown dates stay unknown through commit, that a person's
erasure removes their placements and nobody else's, and that a batch survives an export and restore still
committable. They live in `tests/adapters/importers/test_group_staging.py`,
`tests/adapters/importers/test_group_commit.py`, and `tests/adapters/sqlite/test_bootstrap_group_candidates.py`.
Those are properties of the code, and they are checked on every run.

What they do not cover is whether an agent, reading a real conversation, produces the capture these scenarios
show: whether it asks about an existing group instead of creating a second one, whether it leaves a year out when
the user did not give it, whether it stops at the extent that was confirmed, and whether it reports `unknown` as
unknown. Hand-authored candidates prove the lifecycle accepts them; they prove nothing about the judgement that
chose them, and they prove nothing about the judgement that chose them. That judgement is what a human reviewer
assesses.

## Where this material goes

Two retention boundaries apply, and they are not the same boundary.

**The local server** stores the groups and memberships the user accepted, with their provenance, and nothing of
the conversation that produced them. It parses no prose, and the shared-connection lookup is a read: it writes no
durable record, no audit entry, and no changelog row, and it stores no derived edge. A correction, a closure, a
merge, or a hard forget therefore changes the next lookup with no cleanup job, because there was never a stored
deduction to clean up.

Group identity and each membership carry their own sensitivity. A public group can hold a private membership, and
a visible membership never names a withheld group: disclosure is applied before the lookup infers, counts, or
truncates anything, so a hidden record changes no ordinary result, count, or truncation flag. Evidence ids and
attribution are disclosures too, and are filtered the same way.

**The client** is a separate trust boundary with its own rules. When the agent runs in a cloud-hosted assistant,
the whole conversation reaches that provider as ordinary prompt content — what the user said about their class,
their team, their household, and whatever the tool calls returned. The provider's retention terms govern all of
it, and this repository's guarantees do not reach that far. Do not describe a capture as local-only because the
server stored only the accepted candidates; what the server kept says nothing about what the client kept.

See [privacy-and-safety.md](privacy-and-safety.md) for the disclosure model and
[use-cases/README.md](use-cases/README.md) for the same split stated for the CLI and agent recipes.

## Related reading

- [Human review of shared-context capture](evals.md#human-review-of-shared-context-capture) — the rubric these
  cases are written to be assessed against, and why no automated check in this repository grades them.
- [import.md](import.md#capturing-a-shared-context-m283) — the delivered capture workflow and the lifecycle these
  candidates go through.
- [mcp-interface.md](mcp-interface.md) — `explain_shared_connections` and the group management tools.
- [transcript-review-examples.md](transcript-review-examples.md) — another workflow assessed this way.
- [communication-coaching-examples.md](communication-coaching-examples.md) — and the third.
