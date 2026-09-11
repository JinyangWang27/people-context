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

Illustrative draft:

> 林经理，报告目前完成到第三节，剩下迁移风险和回滚方案两部分。这周二到周四我在处理支付链路的线上问题，那段时间没能推进。
> 我可以周五下班前交完整版，或者明天先给您前三节让您先过一遍。您看哪个更合适？

**Lesson.** Naming what took the time and naming a new date are two different moves, and the second one is what
the manager needs. Supplying both, in that order, lets the explanation read as information rather than as a
defence — and offering a partial deliverable gives her something to choose between instead of something to accept.

**Not done.** No apology the user did not want to make, no promise about work that has not been scoped, and no
mention of who owned the incident. The agent wrote nothing to the store.

## 2. Declining scope the user will not absorb (English)

**Situation.** Bram Tesfaye, a peer on another team, has asked the user to take over two dashboards "since you are
already in that code". The user has decided to say no and wants the refusal to be clear without souring a working
relationship they still need.

> I'm going to say no to Bram about the dashboards. Help me write it so it's final but not hostile.

The agent reads the stored context and finds nothing that argues for softening. The user has said the decision is
made, so the agent drafts a refusal rather than negotiating on Bram's behalf.

Illustrative draft:

> Bram — I can't take the dashboards on. My quarter is committed to the billing migration and adding these would
> put that at risk, so this is a no rather than a not-yet. Happy to spend an hour walking whoever picks them up
> through the parts of the code I know well.

**Lesson.** A refusal is clearer when it says which one it is. "Not right now" invites a follow-up in three weeks;
"this is a no rather than a not-yet" closes the question, and the offer of an hour shows the no is about capacity
and not about him.

**Not done.** The agent did not invent a future date to soften the refusal, did not suggest escalating to a
manager, and did not treat the user's decision as something to talk them out of. Social skill is not compulsory
appeasement.

## 3. Repairing a friendship after a missed occasion (English)

**Situation.** The user forgot Noor Haddad's thirtieth birthday. Two weeks have passed and neither has mentioned
it. The user does not want to send something that reads as an excuse.

> I completely missed Noor's birthday and it's been two weeks. I want to say something real, not a Hallmark
> apology. I'm not a gushy person.

The agent reads `get_person_context` for Noor and finds a stored preference for voice notes over text. It offers
that as a genuine choice with a cost attached, rather than presenting it as the correct answer.

Illustrative draft:

> I missed your birthday and I've been sitting with that for two weeks, which made it worse. No excuse — I just
> didn't have it written down anywhere. I'd like to take you out properly. Are you free either of the next two
> weekends?

Alternative, which trades immediacy for warmth: send the same thing as a voice note, since Noor has said she
prefers them. That costs the user a day if they want to record it when they are not rushing, and it makes the
apology harder to skim past.

**Lesson.** An apology that includes a repair is easier to accept than one that only carries regret. Naming the
delay before she does removes the one thing she might otherwise have to raise.

**Not done.** No manufactured warmth, no reference to whatever else the store knows about Noor, and no
explanation dressed up as an apology. The user said they are not gushy, and the draft is not gushy.

## 4. Holding a boundary with family (Chinese)

**Situation.** 姑妈周慧兰 (Aunt Zhou Huilan) has asked the user for the third time this month to come home for a
matchmaking meeting over the holiday. The user is not going and does not want to relitigate the reasons.

> 我姑妈又让我回去相亲。我不想去，也不想再解释一遍理由。能不能帮我说得客气但是别留口子。

The stored philosophy text says the user values 直接但不伤人 (direct but not wounding). The agent writes to that
without turning it into a rule about how families should work.

Illustrative draft:

> 姑妈，谢谢您一直惦记着我。这件事我自己会安排，今年过年就不安排相亲了。我腊月二十八回来，到时候陪您吃饭。

**Lesson.** A boundary holds better when the refusal and the relationship are in the same message. Declining the
meeting while committing to the visit says what the user is turning down without implying they are turning down
her.

**Not done.** The agent did not tell the user to go, did not explain their reasons on their behalf, and did not
read family obligation off their name or their language. Cultural context came from the user's own philosophy
text, not from an assumption about Chinese families.

## 5. A terse message with unreadable intent (English)

**Situation.** Ivo Karlsen replied to a long proposal with one word: "Noted." The user reads it as cold and wants
to know what it means before responding.

> Ivo just replied "Noted." to my whole proposal. Is he annoyed with me?

The agent reads what is stored and finds two recent interaction summaries under `friction_notes`, both ordinary.
It says so rather than treating the field name as a finding.

The honest answer has no diagnosis in it:

> I can't tell you what he meant, and neither can the store. "Noted." carries at least three readings: he read it
> and has no objection, he is deferring a real response until he has time, or he disagrees and is not saying so
> yet. Nothing recorded about him distinguishes these — the two most recent interactions I can see are a routine
> scheduling exchange and a handover note, neither of them tense.

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

> I can't reach your people-context server right now, so I have nothing stored about Sam — no past dealings, no
> recorded preferences. I can still help with the negotiation from what you've told me; just treat the advice as
> general rather than tailored to them.

The coaching that follows is built entirely from what the user supplied in the conversation.

**Lesson.** Missing context changes what advice can claim, not whether advice is possible. Saying which one you
are giving is the part that matters.

**Not done.** No guessed identity in order to have something to read. No person created in order to have
somewhere to write. No general advice presented as though it were grounded in records.

## 7. Rehearsal and debrief (English)

**Situation.** The user has a skip-level with Aditi Rao tomorrow and wants to practise asking for a title change.
Afterwards, they come back and say it went badly.

> Can you be Aditi and let me practise? I want to ask about the staff title.

The agent plays the part and labels every reaction as what it is:

> Playing Aditi, and this is a guess at how she might respond, not a prediction:
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

**Not done.** The simulated Aditi was never described as what Aditi would say. The debrief offered readings
instead of a verdict, and did not tell the user a better script would have prevented the outcome.

## 8. Saving an outcome, when asked (English)

**Situation.** After the skip-level, the user asks to record what they learned.

> Save this: Aditi cares about cross-team scope for staff promotions, and she told me to come back in Q3.

Two different things are in that sentence, and they take two different paths.

The second half is something Aditi told the user directly, so it goes down the direct-capture path — `remember`
resolves the name, records the one statement, and reports ambiguity rather than guessing. The first half is the
user's reading of what she cares about; it is staged with `stage_candidates` and waits for `review_import` and an
explicit acceptance before `commit_import`. The agent says which is which before writing anything.

What does not get recorded: the rehearsal, the simulated replies, the drafts, and the user's report that the
conversation went badly. A draft is not an outcome and a simulated reaction is not observed behaviour; neither
becomes an `observation`, and one difficult conversation is not a `trait`.

If the user had instead said "remind me to raise this in Q3", that is a `set_reminder` they asked for or a line in
the reply — there is no reminder candidate type, so it is never staged.

**Lesson.** What someone told you and what you concluded about them are different kinds of claim, and a store that
keeps them apart stays correctable later.

**Not done.** Nothing was written until the user asked. No end-of-session capture was proposed during the coaching
itself, because a drafting session is not a source of durable knowledge about anyone.

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
