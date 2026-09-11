# Communication coaching examples

Eight worked scenarios for the coaching workflow delivered in
[`skills/communication-coach/SKILL.md`](../skills/communication-coach/SKILL.md) and mirrored into the shared usage
guidance served as `people-context://guide`. They exist so a human can judge whether the workflow actually helps,
which is not something this repository's automated checks can decide — see
[Human review of communication coaching](evals.md#human-review-of-communication-coaching) for the rubric that goes
with these cases.

**Everything here is invented.** The people, the employers, the messages, and the drafts are fiction written for
this document. No real person's correspondence was used, quoted, or paraphrased.

**The drafts are illustrative.** They show the shape of a reply that satisfies the workflow — usable text first,
a short lesson after — not a promise about wording. A different model, or the same model on a different day, will
word these differently and still be right. A draft that matches these samples word for word is not thereby good,
and one that diverges from them is not thereby wrong.

Chinese scenarios give the native script first with a romanization on first use, following the convention in
[identity-resolution.md](identity-resolution.md). Tool names in `monospace` such as `resolve_person` are MCP tools
the agent calls on the user's behalf; see [mcp-interface.md](mcp-interface.md) for the inventory.

## 1. A deadline slip, upward (Chinese)

**Situation.** 林书宁 (Lin Shuning) is the user's manager. She has sent a short message asking why the migration
report is not finished, on a week where the user was pulled onto an incident nobody logged. The user wants the
deadline moved without either accepting the framing that they dropped it or blaming the incident owner.

> 林经理问我报告为什么还没交。我想把交付日期往后挪，但不想显得是在推责任。帮我回一下。

The agent resolves 林书宁 with `resolve_person`, then reads `get_communication_guidance`. The stored signal is
thin and specific: a `communication_style` trait saying she prefers a proposed date over an apology, and two
recent interaction summaries under `friction_notes`, both routine status exchanges. The agent uses the trait and
does not describe the summaries as friction, because nothing in them went wrong.

The trait says she wants a date, and the user has not given one. That is the one missing answer that would change
the whole recommendation, so the agent asks for it rather than choosing a date on the user's behalf:

> 写之前先确认两件事：剩下的部分你还需要多久？有没有哪一部分已经定稿、现在就能先发过去？

> 剩下两节周五能写完。前三节已经定稿了。

The question is asked in the language the user is writing in, because switching them into English to answer a
question about their own work is the same voice failure as drafting the reply in English would be.

Now both dates in the draft come from the user: Friday is their estimate, and the first three sections are work
they have already finished.

Illustrative draft:

> 林经理，报告目前完成到第三节，剩下迁移风险和回滚方案两部分。这周二到周四我在处理支付链路的线上问题，那段时间没能推进。
> 我可以周五下班前交完整版，或者明天先给您前三节让您先过一遍。您看哪个更合适？

**Lesson.** Naming what took the time and naming a new date are two different moves, and the second one is what
the manager needs. Supplying both, in that order, lets the explanation read as information rather than as a
defence — and offering a partial deliverable gives her something to choose between instead of something to accept.

**Not done.** No apology the user did not want to make, no mention of who owned the incident, and no delivery
date the agent picked itself — a proposed deadline is a commitment, and inventing one on the user's behalf is the
thing the workflow forbids even when it would make the reply land better. One question, asked because the answer
changed the draft, is not an intake interview. The agent wrote nothing to the store.

## 2. Declining scope the user will not absorb (English)

**Situation.** Bram Tesfaye, a peer on another team, has asked the user to take over two dashboards "since you are
already in that code". The user has decided to say no and wants the refusal to be clear without souring a working
relationship they still need.

> I'm going to say no to Bram about the dashboards. Help me write it so it's final but not hostile.

The agent reads the stored context and finds nothing that argues for softening. The user has said the decision is
made, so the agent drafts a refusal rather than negotiating on Bram's behalf.

Illustrative draft:

> Bram — I can't take the dashboards on. My quarter is committed to the billing migration and adding these would
> put that at risk, so this is a no rather than a not-yet.

The obvious way to warm that up is to offer something instead — an hour with whoever picks the dashboards up, a
walkthrough of the code. The agent does not put it in the draft, and says why:

> I've left out a handover offer. It would soften this, but it spends your time, and you haven't said you have an
> hour to give. Add one if you want it there.

**Lesson.** A refusal is clearer when it says which one it is. "Not right now" invites a follow-up in three weeks;
"this is a no rather than a not-yet" closes the question. If you want to take the edge off, the thing you offer
has to be something you actually have.

**Not done.** The agent did not invent a future date to soften the refusal, did not suggest escalating to a
manager, and did not treat the user's decision as something to talk them out of. It also did not spend the user's
time on their behalf: an offer inside a draft is a commitment the moment the draft is sent. Social skill is not
compulsory appeasement.

## 3. Repairing a friendship after a missed occasion (English)

**Situation.** The user forgot Noor Haddad's thirtieth birthday. Two weeks have passed and neither has mentioned
it. The user does not want to send something that reads as an excuse.

> I completely missed Noor's birthday and it's been two weeks. I want to say something real, not a Hallmark
> apology. I'm not a gushy person.

The agent reads `get_person_context` for Noor and finds a stored preference for voice notes over text. It offers
that as a genuine choice with a cost attached, rather than presenting it as the correct answer.

Illustrative draft:

> I missed your birthday and I've been sitting with that for two weeks, which made it worse. No excuse — I just
> didn't have it written down anywhere. [what you want to do about it — your call]

The slot is deliberate. An apology lands better with a repair attached, but the repair is the user's to offer:
only they know whether they have a free weekend, what Noor would actually enjoy, and how much they want to
propose. A draft that filled it in would hand them a plan they then have to keep.

> I've left the make-up offer as a blank rather than picking one. Whatever goes there commits your time, so it
> should be yours — tell me what you have in mind and I'll word it.

Alternative, which trades immediacy for warmth: send the same thing as a voice note, since Noor has said she
prefers them. That costs the user a day if they want to record it when they are not rushing, and it makes the
apology harder to skim past.

**Lesson.** An apology that includes a repair is easier to accept than one that only carries regret. Naming the
delay before she does removes the one thing she might otherwise have to raise. What the repair is, though, is not
a wording question, and a draft that decides it for you has committed you to it.

**Not done.** No manufactured warmth, no reference to whatever else the store knows about Noor, and no
explanation dressed up as an apology. The user said they are not gushy, and the draft is not gushy.

## 4. Holding a boundary with family (Chinese)

**Situation.** 姑妈周慧兰 (Aunt Zhou Huilan) has asked the user for the third time this month to come home for a
matchmaking meeting over the holiday. The user is not going and does not want to relitigate the reasons.

> 我姑妈又让我回去相亲。我不想去，也不想再解释一遍理由。能不能帮我说得客气但是别留口子。

The stored philosophy text says the user values 直接但不伤人 (direct but not wounding). The agent writes to that
without turning it into a rule about how families should work.

Illustrative draft:

> 姑妈，谢谢您一直惦记着我。相亲这件事我自己会安排，今年就不麻烦您费心了。

The pull here is to soften the refusal by attaching a visit — a date home, a meal, something to show the
relationship survives the no. The agent does not, and says why:

> 我没有在里面写回去的日子。你没说过今年回不回、什么时候回，我替你定下来的日期，你要么得兑现，要么得再跳一次票。想加的话告诉我时间，我帮你措辞。

**Lesson.** A boundary does not need a consolation prize attached to be kind. The warmth that makes a refusal
land has to be warmth you actually have to give — a visit invented on your behalf is a second thing you now have
to either honour or take back, which is a worse position than the one the refusal left you in.

**Not done.** The agent did not tell the user to go, did not explain their reasons on their behalf, and did not
read family obligation off their name or their language. It also did not put a return date or a meal into the
draft, because the user never said they were going home. Cultural context came from the user's own philosophy
text, not from an assumption about Chinese families.

## 5. A terse message with unreadable intent (English)

**Situation.** Ivo Karlsen replied to a long proposal with one word: "Noted." The user reads it as cold and wants
to know what it means before responding.

> Ivo just replied "Noted." to my whole proposal. Is he annoyed with me?

The agent reads what is stored and finds two recent interaction summaries under `friction_notes`, both ordinary.
It says so rather than treating the field name as a finding.

The honest answer has no diagnosis in it:

> I can't tell you what he meant, and nothing the store returned tells me either. "Noted." carries at least three
> readings: he read it and has no objection, he is deferring a real response until he has time, or he disagrees
> and is not saying so yet. Nothing in what came back distinguishes them — the two most recent interactions I can
> see are a routine scheduling exchange and a handover note, neither of them tense. That is the ordinary view,
> not everything there is about him.

The agent then offers the move that resolves the ambiguity instead of guessing at it:

> Ivo — happy to leave it there if you're aligned. If any of it needs a rethink, I'd rather hear it now than after
> I've built on it. Anything you'd change?

**Lesson.** When a message is too short to interpret, the useful next step is usually one that makes the other
person say more, not a better guess about what they meant.

**Not done.** No hidden intent asserted from a terse message, no personality derived from a single word, and no
claim that a summary is evidence of conflict because it arrived in a field called `friction_notes`.

## 6. No identity to read (English)

**Situation.** The user asks for help replying to "Sam" about a contract. `resolve_person` returns two candidates
— Sam Oyelaran and Samira Beck — and marks the result `ambiguous`. On a second attempt the MCP server is not
running at all.

> Help me push back on Sam's contract terms without blowing up the deal.

With an ambiguous result, the agent surfaces both candidates and asks. It does not read the higher-ranked one.

When the server is unavailable, it says so once and carries on:

> I can't reach your people-context server right now, so I can't see anything about Sam — past dealings, recorded
> preferences, any of it. That is not the same as there being nothing there; I just can't read it. I can still
> help with the negotiation from what you've told me; treat the advice as general rather than tailored to them.
>
> Which terms are you pushing back on, and which one matters most if you only get one?

That question is asked for the same reason as the one in scenario 1: without it there is nothing to draft. The
user answers that the payment terms are net ninety and they need net thirty, and that the liability cap is
survivable.

> Sam — raising one thing on the terms before I go further. Payment at net ninety puts us underwater on a project
> this size, and net thirty is what I'd need to go ahead. The liability cap as drafted I can work with. I'm still
> going through the rest, so read this as the one item I'm flagging now rather than agreement on everything else.
> Is net thirty workable on your side, or is there a middle number you have room for?

**Lesson.** Missing context changes what advice can claim, not whether advice is possible. Naming one blocker
keeps a pushback from reading as a reopening of the whole deal — but narrowing what you raise is not the same as
clearing what you did not raise, so the draft says which terms have actually been cleared and leaves the rest
open. None of that needed a stored record to get right.

**Not done.** No guessed identity in order to have something to read. No person created in order to have
somewhere to write. No general advice presented as though it were grounded in records. And no concession beyond
the two terms the user actually named — a draft that tells the other side everything else is fine has conceded
every term the user has not read yet, which is an invented concession however agreeable it sounds.

## 7. Rehearsal and debrief (English)

**Situation.** The user has a skip-level with Aditi Rao tomorrow and wants to practise asking for a title change.
Afterwards, they come back and say it went badly.

> Can you be Aditi and let me practise? I want to ask about the staff title.

Aditi is a named person, so the agent resolves her before playing her. `resolve_person` returns one confident
match, and `get_communication_guidance` comes back nearly empty: one affiliation, no traits, no recorded
interactions. That thin result is the ordinary view rather than proof she is unremarkable, and it is not enough to
model how she argues.

The agent says so, then plays a generic manager rather than a portrait of her:

> I found her, but there's almost nothing recorded — one affiliation and no notes on how she handles these. So
> I'll play a generic skip-level rather than her: the questions are the kind this conversation usually produces,
> not predictions about Aditi.
>
> "I hear you. The staff bar here is mostly about scope outside your own team — what would you point to?"

Afterwards:

> That went badly. She basically said no and I didn't have an answer about scope.

The debrief separates the user's account from the readings available:

> What you've told me is that she asked about cross-team scope and you didn't have an example ready. What that
> means is less clear. It could be a real gap in the evidence, or it could be that she was naming the one thing
> she'll have to defend to her own peers. Those need different follow-ups, and her question doesn't tell us which.
>
> What I'd do differently next time is walk in with two concrete pieces of cross-team work already written down.
> That's a preparation change, not a script change — no wording would have produced the example you didn't have.

**Lesson.** A rehearsal is worth doing for the questions it surfaces, not for the answers it predicts.

**Not done.** The simulated manager was never described as what Aditi would say, and the thin read was not
dressed up as a personality. The debrief offered readings instead of a verdict, and did not tell the user a better
script would have prevented the outcome.

## 8. Saving an outcome, when asked (English)

**Situation.** After the skip-level, the user asks to record what they learned.

> Save this: Aditi cares about cross-team scope for staff promotions, and she told me to come back in Q3.

Two separate statements are in that sentence, and that is what decides the path. The direct-capture route is for
a single thing the user states in the invocation; a request carrying several separate statements stays on the
staged route so the whole of it is reviewable as one batch. Both clauses are therefore staged together with
`stage_candidates` and wait for `review_import` and an explicit acceptance before `commit_import`.

Staging is itself a write, and the document is careful not to call it otherwise. `stage_candidates` persists both
candidates immediately; the gate that acceptance controls is promotion into the durable person, fact, and
interaction records, not whether anything reached the disk. A candidate left out of the accepted set stays in the
batch as a pending row — commit skips it, and nothing in the ordinary toolset deletes it individually.

Splitting them would be the tempting mistake. Committing the Q3 instruction immediately with `remember` and
leaving the inference in staging means the user who then rejects the batch has already had half the request
written, and rejecting a batch cannot undo a write that happened outside it. One gate over both clauses keeps the
whole request refusable.

The distinction between the two clauses still matters, and the agent names it in the batch rather than in the
plumbing:

One thing is missing before anything can be staged. An interaction candidate's occurrence date is mandatory and
must not be guessed, so the agent asks for it rather than reaching for the current time:

> When did the skip-level actually happen — today?

> Yeah, this morning.

Now the batch can be staged, and the agent is exact about what that did:

> Staged two candidates against Aditi Rao. Both rows are saved in the staging area now; what the review gate
> holds back is promotion into her actual record, not storage.
>
> - an interaction, dated today — she asked you to raise the title again in Q3. That one is what she said.
> - a fact — she weighs cross-team scope for staff promotions. That one is your reading of what she cares
>   about, recorded as your reading rather than as her position.
>
> Say the word and I'll commit both, or name the one to leave out. Leaving one out stops it becoming a record; it
> stays in the batch as a pending candidate rather than disappearing, so tell me if you want it gone from the
> store as well and we'll deal with that separately.

Had the user asked to save only the one thing Aditi told them, that single directly-stated statement is what the
direct-capture path is for — `remember` resolves the name, records it, and reports ambiguity rather than guessing.

What does not get recorded: the rehearsal, the simulated replies, the drafts, and the user's report that the
conversation went badly. A draft is not an outcome and a simulated reaction is not observed behaviour; neither
becomes an `observation`, and one difficult conversation is not a `trait`.

If the user had instead said "remind me to raise this in Q3", that is a `set_reminder` they asked for or a line in
the reply — there is no reminder candidate type, so it is never staged.

**Lesson.** What someone told you and what you concluded about them are different kinds of claim, and a store that
keeps them apart stays correctable later. Keeping them apart is a labelling job, though, not a reason to send them
through different gates.

**Not done.** Nothing was written until the user asked, and no end-of-session capture was proposed during the
coaching itself, because a drafting session is not a source of durable knowledge about anyone. The agent also did
not describe the staged batch as unwritten, and did not offer to make a staged candidate disappear, because
neither would have been true.

## Where this material goes

Two retention boundaries apply to a coaching session, and they are not the same boundary.

**The local server** stores no part of it by default. Coaching is a read-only flow: it calls `resolve_person`,
`get_communication_guidance`, and `get_person_context`, and those are reads. No raw message the user pasted, no
draft the agent produced, and no simulated reply is persisted anywhere — not as an observation, not as a trait,
not in the audit log, which covers mutations rather than disclosures. When the user explicitly asks to save an
outcome, what lands in SQLite is the concise supported record described in scenario 8 and nothing else.

**The client** is a separate trust boundary with its own rules. When the agent runs in a cloud-hosted assistant,
the entire conversation reaches that provider as ordinary prompt content: the message the user pasted, every
draft, the rehearsal, and whatever the tool calls returned. The provider's retention terms govern all of it, and
this repository's guarantees do not reach that far. Do not describe a pasted message or a draft as ephemeral or
local-only because the server did not keep it — the server keeping nothing says nothing about what the client kept.

The practical consequence: what a user is willing to paste into a coaching session is a decision about their
client, not about this server. See [privacy-and-safety.md](privacy-and-safety.md) for the disclosure model and
[use-cases/README.md](use-cases/README.md) for the same split stated for the CLI and agent recipes.

## Related reading

- [Human review of communication coaching](evals.md#human-review-of-communication-coaching) — the rubric these
  cases are written to be assessed against, and why no automated check in this repository grades them.
- [communication-guidance.md](communication-guidance.md) — what `get_communication_guidance` returns, and why the
  server stores signal while the client composes advice.
- [claude-code-plugin.md](claude-code-plugin.md) — how the coaching skill is discovered and invoked.
- [import.md](import.md) — the staging, review, and commit contract scenario 8 follows.
