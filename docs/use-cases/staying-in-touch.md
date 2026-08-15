# Staying in touch on purpose

**Situation.** The people you speak to most are the people who happen to be loudest that week. The ones who
quietly fell off the calendar six months ago are exactly the ones you meant to keep.

**Goal.** Turn "I should catch up with someone" into a short, specific list, and then into something on a date.

## 1. Ask who has gone quiet

```bash
pctx stale
pctx stale --category professional --threshold-days 120 --limit 10
```

`stale` reports one row per person: their last ordinary interaction and how long ago it was. Three details make
it trustworthy rather than merely suggestive:

- it counts **ordinary interactions only**, so a sensitive record cannot leak into a report through a recency
  count;
- people with **no** recorded interaction sort first, because never having spoken is the strongest signal there
  is, not a missing value to hide;
- a future-dated interaction is not treated as stale, so a scheduled entry cannot make someone look overdue.

Your agent can ask the same thing through `get_stale_relationships` when you would rather say it out loud.

## 2. Look at what is coming

```bash
pctx upcoming --window-days 30
```

`upcoming` merges annual birthdays and dated active reminders into one inclusive window. Partial birthdays — a
day and month with no year — still surface, and a real 29 February is handled as a real leap day rather than
being quietly moved. Elevated facts stay invisible to the counts.

## 3. Turn a name into a commitment

> Remind me to send Ingrid the documentary outline before the end of the month.

The agent calls `set_reminder` against the resolved person. `list_reminders` pulls them back, filtered by person,
due date, or status, and `complete_reminder` closes one out. Reminders are pull-based by design: there is no
daemon, no notification service, and nothing that phones home on a schedule.

## 4. Put them in the calendar you already use

```bash
pctx reminders-ics --output ~/Documents/people-reminders.ics
```

This writes active dated reminders as a deterministic iCalendar file you can subscribe to locally. It exports
only reminders whose times are unambiguous — timezone-aware `due_at` values — and reports counts of what it
skipped (`skipped_undated`, `skipped_naive_datetime`) rather than guessing a timezone on your behalf. The file
is written owner-only and atomically, so a failed write leaves the previous file intact.

It is a one-way local export. Nothing pushes to a third-party calendar service.

## What you should see

`stale` prints a short ordered table, truncated at your limit and telling you when it truncated. If it returns
nothing at a 90-day threshold, that is a real answer: you are current.

## Next

- [Ten minutes before a meeting](before-a-meeting.md) — preparing for the catch-up you just scheduled.
- [cli.md](../cli.md) — every flag for `stale`, `upcoming`, and `reminders-ics`.
