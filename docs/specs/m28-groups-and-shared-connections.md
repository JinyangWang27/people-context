# M28 — Groups, memberships, and shared connections

Status: M28.1 delivered (protected groups and memberships); M28.2 delivered (explained shared connections); M28.3
planned — not implemented.
See [roadmap](../roadmap.md#m28--groups-memberships-and-shared-connections) and
[PR checklist](pr-plan.md#m28--groups-memberships-and-shared-connections).

## Purpose and first principles

Answer “How are A and B connected, through which group, and when?” from recorded evidence. Support school,
work, clubs, households, and communities through one group/membership foundation. The user is an ordinary person
endpoint for this purpose; no self-specific relationship logic is needed.

Current relationships describe typed person-to-person assertions with dates, confidence, and provenance. Custom
vocabulary already supports additional names. Organizations and affiliations describe roles at organizations,
but do not identify a particular class, cohort, or team, and have no sensitivity controls. Neither symmetric
vocabulary nor two edges through a common person establishes transitivity: two people who were my classmates
may have attended different classes or years.

Keep three concepts separate: an identified group, a recorded membership assertion, and a connection derived
from permitted evidence. A group is a context in which people participate, not proof that they know one another.
No automatic belief updater or general-purpose inference engine is required.

## Model and inference boundaries

### Identified groups and memberships

- Groups have stable ids, names, kinds, and optional organizational context. Support classes, cohorts, teams,
  departments, clubs, households, and communities. Equal or normalized names are lookup candidates, not identity
  proof; ambiguity requires explicit resolution. Do not reuse organization name get-or-create as group identity.
- Reuse existing organizations as references when appropriate, without automatically converting old affiliations
  or copying protected group details into unrestricted organization rows. A group must also be representable
  independently when no organization is known or safe to disclose.
- Membership assertions identify the person, group, role, temporal evidence, provenance, confidence, and
  sensitivity. Multiple concurrent and historical memberships are valid. Distinguish correcting an erroneous
  assertion from closing a historically true membership; preserve supporting provenance in either workflow.
- Organizational placement and membership are separate. A cross-company team can include external participants;
  placement under an organization or department does not create employment or department membership. Group
  hierarchy alone creates neither memberships nor person-to-person relationships. Arbitrary hierarchy traversal
  and organizational inference are outside the first version.
- Keep direct relationships, including custom types, for explicit assertions whose group is unknown. “My
  classmate” or “my teammate” alone must not create an invented group or membership. Confirm context first.

### Conservative shared connections

Derive connections at read time from visible memberships in the same identified group. Do not save a pairwise
edge for every co-member pair or give deductions the status of independently recorded assertions. Return the
group context and supporting membership references so the caller can explain the result and its limitations.
Repeated evidence or the inference itself does not increase source confidence.

| Permitted evidence | Supported result |
|---|---|
| Same school, different or unknown classes | Shared school context, not a classmate assertion. |
| Student roles in the same class with established overlap | Classmates during that established overlap. |
| Participant roles in the same team with established overlap | Teammates during that established overlap. |
| Same group, dates unknown or insufficient to prove overlap | Shared group context; contemporaneity unknown. |
| Same group, known disjoint periods | Shared historical group context; no contemporaneous relationship. |
| Same club, household, or community | Shared membership; no inferred friendship, kinship, or acquaintance. |
| Two relationships through a common person, without group evidence | No derived shared membership. |

Roles matter: a teacher and a pupil are not classmates merely because they share a class. The default for a
group kind or role combination without a specific supported rule is an explained shared context, not a guessed
relationship label. A negative lookup means no supporting shared context was found within the permitted scope,
not proof that the people are unrelated or do not know each other.

Current `ValidityPeriod` treats absent bounds as unbounded for filtering. Do not treat that behavior as proof
of real-world overlap. New membership temporal evidence must distinguish unknown dates from an asserted period
or confirmed participation at a known time. Missing dates stay missing; narrower relation labels require an
established common time. Do not change legacy date semantics to implement this distinction.

### Class placement and continuing cohorts

A dated placement such as “Class 1, Grade 6, 2015–2016” is distinct from a continuing cohort. Grade and class
number are explicit context when known; “classmate” alone establishes neither, including in mixed-grade classes
and university courses. Group identity must not be rewritten each year to erase previous placements.

If the user confirms “we stayed together through Grade 9,” capture the confirmed cohort continuity assertion
with its attribution and known temporal extent. Do not generate annual placements, guessed dates, class numbers,
or an assumed unchanged roster. A narrower confirmation about three people applies only to those people.

Progression from Grade 6 in 2015–2016 to a later grade is a conversational hypothesis until confirmed. Transfers,
reshuffling, repeated/skipped grades, and calendar differences prevent treating likely progression as fact.
Even a proposed next year must not silently skip an academic year. Asking about continuity is allowed; persisting
speculative memberships or automatically advancing grades is outside M28.

## Disclosure and explicit lookup

Group identity, membership assertions, and supporting context each need enforceable sensitivity using the
existing levels. Default new protected records to `personal`; sensitive and restricted content stays out of
ordinary reads. A public group may still have a private membership, and a visible membership must not expose
a withheld group name or supporting assertion. Sensitive context must not leak into existing unrestricted
relationships, affiliations, organization names, summaries, or provenance strings.

Apply caller disclosure policy before inference, ranking, counting, and result truncation. Hidden records must
not change visible explanations, counts, or truncation signals. Evidence ids and attribution are disclosures too.
The new lookup is an ordinary-disclosure read; do not introduce a broadly elevated MCP lookup. Preserve existing
operator-controlled export/inspection boundaries without implying that agent input can elevate access.

Plan one additive bounded CLI/MCP lookup for two resolved people. Results distinguish shared context from a
derived relation, explain temporal certainty, and cite only permitted supporting records. Direct assertions remain
distinguishable from deductions; preserve the existing direct-relationship reads. Stable ordering, explicit
truncation, and query-work bounds are required, without expanding the query into every pair in a large group.

Agents should invoke this lookup for shared-background, introduction, and connection questions; users need not
name a tool. Do not automatically add inferred classmates or colleagues to ordinary person context, graph edges,
or shortest-path results. Existing tools keep their meanings. Reads write no durable records, audit, or changelog.

## Planned PRs and compatibility

### M28.1 — Store protected groups and memberships

Add domain values, narrow ports, additive SQLite persistence, and bounded CLI/MCP management for groups and
memberships, including explicit correction and historical closure. Implement privacy filtering and validation
with the first read/write surfaces, not in a later hardening PR. Preserve ambiguity, multiple roles, uncertain
dates, and confirmed cohort continuity without fabricating detail.

Every durable mutation participates in the existing transaction/audit/changelog seam. Include export/bootstrap
round trips and compatible versioned envelopes, referential validation, person merge, and hard-forget handling
before exposing writes. New stores must not silently lose groups or memberships through portability. Merge must
not manufacture stronger claims; forget must remove affected person-linked evidence and follow existing payload
scrubbing rules while preserving other people's valid records. Use synthetic data for all validation.

No automatic conversion of old affiliations or relationships. Preserve current contracts, record fields, and
disclosure semantics. New schema uses the next free additive migration at implementation time; evaluate machine
format changes against the compatibility promise. No dependency or release/version change is made by this spec.

### M28.2 — Explain shared connections

Depends on M28.1. Deliver the explicit pairwise CLI/MCP lookup with the role/time rules above, permitted evidence
references, privacy-before-inference, deterministic limits, and bounded work. Corrections, membership endings,
and forget operations must affect the next read without a derived-edge cleanup job. Preserve existing graph/path
and ordinary context behavior. Verify connected-looking inputs that do not support stronger conclusions.

### M28.3 — Capture and use shared context through agents

Depends on M28.2. Extend the existing reviewed staging lifecycle for group/membership assertions with bounded,
versioned candidate contracts, group identity resolution, attribution, source receipts, and explicit accepted
commits. Group references within a batch must resolve before dependent memberships are committed; partial success
must be reported accurately. Integrate new staged state with restore, merge, and forget as existing candidates do.

Add shared/packaged agent guidance and fictional end-to-end examples across school, work, and social contexts.
Unknown groups or dates remain unresolved; no raw sources or speculative progression become persisted metadata.
Continuity suggestions are conversational until confirmed, and confirmation of a source interpretation is not
approval to commit extracted records. Preserve stage → review → explicit acceptance → commit and guide parity.

## Acceptance scenarios and verification

- Same class with proven overlap, same school with different classes, unknown dates, known disjoint dates,
  teacher/student roles, mixed-grade classes, and incomplete group membership information.
- Same team, different teams, cross-company projects, changing departments, and concurrent roles. Organizational
  placement must not imply employment or person-to-person ties.
- Clubs, households, and communities without inferred friendship, family, or acquaintance. Equal group names
  remain distinct when identity is unresolved; conflicting sources retain their provenance and uncertainty.
- Confirmed cohort continuity with known and unknown bounds; no invented yearly placements, shifted academic
  dates, unchanged whole-class roster, or automatic advancement from a single known placement.
- Sensitive group identity, sensitive membership in a public group, and hidden evidence behind visible records.
  Adding/removing hidden evidence must not alter ordinary results, counts, explanations, or truncation flags.
- Corrections, historical closure, person merge, and hard forget remove stale deductions on the next lookup.
  Export/restore preserves source evidence and privacy; legacy inputs retain their supported behavior.
- Bounded pairwise lookup for large groups, deterministic output, honest partial results, no materialized inferred
  edges, no read-time writes, and unchanged existing graph/path results.
- Reviewed capture resolves people/groups, preserves attribution and temporal uncertainty, rejects unsupported
  references, respects acceptance and partial-commit semantics, and never persists raw source content.

Implementing PRs run focused fake-port, SQLite, CLI/MCP, privacy, lifecycle, and portability checks, followed by
repository-required Ruff, mypy, pytest, and packaging checks for public surfaces. Guidance quality needs fictional
conversation review separately from tests over hand-authored candidates. Documentation preparation checks links,
status consistency, and whitespace only; it does not claim runtime scenarios already pass.

## Alternatives and deferred work

- More vocabulary is useful for explicit assertions but cannot identify which shared class/team makes them true.
- Free-text labels are descriptions, not stable group identity or a reliable basis for joins.
- Automatically materializing all pairwise relationships duplicates evidence, grows quadratically, and leaves
  stale assertions after correction or forget. Derive the requested pair from primary evidence instead.
- Reusing affiliations unchanged would lose protected membership semantics. Reuse existing organization references
  and infrastructure, not their unrestricted disclosure contract.
- A general inference engine, automatic group merging, arbitrary hierarchy inference, inferred graph traversal,
  grade progression, and durable speculative memberships remain outside this milestone.

M28 is independent of M27's database location change; neither implementation is delivered by this combined
documentation PR.
