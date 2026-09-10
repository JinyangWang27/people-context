# M22 — Source-grounded CV and background capture

Status: Delivered — M22.1 and M22.2 merged.
See [docs/roadmap.md](../roadmap.md#m22--source-grounded-cv-and-background-capture).

## Purpose

Store attributed knowledge about a person, preserve uncertainty and change, and assemble useful views from those
records. A CV, biography, or set of notes supplies assertions from a source; its contents are not independently
verified truth merely because an agent can extract them.

Reuse the calling agent's document-reading capabilities and the existing stage → review → commit workflow from
[M17](m17-agent-assisted-knowledge-extraction.md), with [M18](m18-provenance-idempotency-and-evidence.md) source
receipts. The agent distills claims; People Context validates and stores accepted candidates.

## Scope and representation

- Represent employment and education as affiliations when their role, organization, dates, and disclosure can be
  represented faithfully. Represent qualifications, skills, and other background as facts.
- Attribute a source's assertions explicitly. A fact such as “Describes herself as analytical in her CV” records
  the self-description; it must not become “analytical” as an inferred temperament. Source attribution does not
  establish the claim's truth or justify high confidence.
- Preserve observations as subjective point-in-time notes and traits as derived characteristics with their existing
  evidence requirements. Do not redefine either type to accommodate promotional CV language.
- Keep background and sensitive details out of the unrestricted person summary. Affiliations and relationships have
  no durable sensitivity field; use appropriately protected facts for details requiring elevated disclosure instead
  of putting those details into ordinary affiliations, graph edges, organization names, or summaries.
- Keep raw documents outside People Context. Distilled values and attribution are concise, not copied document
  passages or raw-content substitutes.

### Dates and historical meaning

Use existing `valid_from` and `valid_to` fields for exact dates the source actually supports. Inclusive validity
semantics remain unchanged. Keep year-only, month-only, approximate, and unknown dates explicit in concise claim
text: for example, “CV reports study at Northbridge College, 2017–2019; months and days unknown.” Never invent
January 1, month boundaries, or an event date derived from the import timestamp.

An unbounded validity period can appear current in existing reads. A historical role with unknown bounds therefore
must not become an apparently current affiliation. When the period cannot be represented faithfully, capture an
attributed background fact with its uncertainty in the value, or leave the proposal unresolved. Do not put guessed
dates into structured fields to force an affiliation. An explicit source claim of a current role may be captured as
such, with attribution and its known bounds; silence about an end date alone does not establish current employment.

## Planned PRs

### M22.1 — Preserve attribution in extracted claims

Add optional `stated_by` support to fact and affiliation candidates. Preserve it through candidate validation,
staging, review, and commit, forwarding it to the existing fact/affiliation write inputs and their provenance and
audit paths. Distinguish this assertion attribution from the processing `source`, process `session`, and the M18
`source_session_id` receipt. Unknown attribution stays absent; do not invent a speaker or replace processing metadata.

Candidates that omit `stated_by`, including previously staged batches, remain valid and retain their existing
behavior. Use the current strict candidate vocabulary and applicable input bounds; no new primary table or durable
provenance schema is needed. This PR does not broaden other candidate types or alter identity-matching semantics.

### M22.2 — Document and exercise agent CV capture

Extend shared agent guidance with this workflow and fictional end-to-end examples:

1. Read the document using the agent's available capabilities. Say when it is unreadable or only partly readable;
   extract only supported claims and never present a partial read as a complete CV.
2. Resolve the subject and relevant identities before proposing durable knowledge. Keep ambiguous matches unresolved;
   do not turn an ambiguous person into a new identity or attach claims to an arbitrary match. Respect differences
   between existing staging surfaces rather than assuming all legacy batches enforce ambiguity automatically.
3. Distill attributed claims with faithful dates, appropriate record types, and enforceable sensitivity.
4. Use existing source-receipt metadata where available. A digest must describe bytes actually available to the
   agent; a receipt is evidence of processing, not independent verification of the CV's claims. Follow existing
   source deduplication and redaction rules; labels must not carry private source content or paths.
5. Stage, show the review with uncertainty and reading limitations, then commit only explicitly accepted candidates.
6. Read back the result and report committed and unresolved items accurately. Repeated or conflicting CVs requiring
   maintenance follow [M24](m24-reviewed-person-updates.md), without silent overwrites during extraction.

Guidance belongs in the existing shared agent surfaces and their examples; no separate CV extraction service is
introduced. Examples must include both successful capture and refusal to guess.

## Acceptance scenarios and verification

- An attributed skill assertion and an exactly dated affiliation survive stage → review → commit with `stated_by`
  in existing provenance. A legacy candidate without it still commits with the prior behavior.
- A fictional CV saying “analytical” produces an attributed self-description, never an inferred temperament or
  personality assessment. Subjective notes and supported traits keep their existing meanings.
- Year-only, month-only, approximate, and missing dates remain explicit in claim text. Historical study or employment
  with unknown bounds creates no apparently current unbounded affiliation; recording dates remain recording dates.
- An unreadable file produces a limitation report and no guessed candidates. A partially readable CV yields only
  supported candidates, with the review stating which material could not be assessed.
- Two people sharing a name remain unresolved until identity is established. Accepted dependent claims cannot be
  knowingly committed to an arbitrary person through a legacy matching path.
- Reprocessing the same tracked source observes existing receipt/idempotency behavior. A revised or conflicting CV
  is additional evidence, not permission to replace older records or increase confidence by counting repetitions.
- Concurrent professional roles are retained when supported; omission from a CV closes or deletes nothing. If an
  update target changes after review, use M24's renewed-review rule before applying a change.
- Sensitive background is stored only through a record type that enforces its disclosure. Ordinary reads reveal no
  sensitive summary, affiliation, relationship, or attribution copied from such a claim; existing history remains.
- Focused candidate, commit/provenance, compatibility, and fictional workflow checks exercise the above behavior.
  Verify source receipts and diagnostics contain no raw-document sentinel. Run the repository's required checks for
  implementation PRs; this milestone specification itself changes documentation only.

## Dependencies, compatibility, and deferred work

M22 reuses delivered M17 extraction and the available M18 source-receipt contracts; M22.2 follows M22.1. M22 precedes
M24 and does not require M23. Existing write semantics, disclosure rules, and candidate envelopes remain compatible.

No native PDF/DOCX parser, internal LLM, raw-document storage, or personality assessment system is included.
Structured partial-date storage, dedicated life events, automated belief revision, semantic deduplication, and
generic batch mutation are deferred. Concise text preserves uncertainty now; it does not provide structured
partial-date queries or an automatic historical transition engine.
