import { describe, expect, it } from "vitest";

import { DocumentFormatError, parseBrief, parsePersonIndex } from "./documents.js";

const INDEX = {
  format: "people-context-person-index",
  version: 1,
  generated_at: "2026-08-11T08:09:14.776653Z",
  include_deleted: false,
  people: [
    {
      id: "01KZQXWK571FJAF03F6H63A85Z",
      canonical_name: "Amina Hassan",
      aliases: ["amina.hassan@example.test"],
      summary: "Research partner.",
      is_self: false,
      deleted: false,
    },
  ],
};

const BRIEF = {
  format: "people-context-brief",
  version: 1,
  generated_at: "2026-08-11T08:09:18.333078Z",
  disclosure: {
    include_sensitive: false,
    context: "ordinary",
    guidance: "ordinary",
    notice: "This brief is a local export.",
  },
  person: {
    id: "01KZQXWK571FJAF03F6H63A85Z",
    canonical_name: "Amina Hassan",
    aliases: ["amina.hassan@example.test"],
    summary: null,
    is_self: false,
  },
  relationships: [
    {
      relationship: { label: "since 2019" },
      other_person_id: "01KZQXWK58QY1T6M8CETCXZVM0",
      other_person_name: "Daniel Okafor",
      display_type: "colleague_of",
    },
  ],
  affiliations: [{ affiliation: { role: "Researcher" }, organization_name: "Civic Loom" }],
  facts: [{ predicate: "preferred_update_style", value: "Concise written updates" }],
  interactions: [{ summary: "Quarterly planning", occurred_at: "2026-06-10T09:00:00Z" }],
  traits: [{ category: "communication_style", value: "Direct" }],
  reminders: [
    { kind: "follow_up", text: "Send the notes", due_at: "2026-09-01T09:00:00Z", recurrence: null },
  ],
  guidance: {
    disclosure: "ordinary",
    traits: { tone: [{ value: "Warm" }], approach: [{ value: "Structured" }] },
    friction_notes: ["Quarterly planning"],
    communication_philosophy: "Be brief.",
  },
};

function indexText(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...INDEX, ...overrides });
}

function briefText(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...BRIEF, ...overrides });
}

describe("parsePersonIndex", () => {
  it("reads the entries the CLI emits", () => {
    const document = parsePersonIndex(indexText());

    expect(document.version).toBe(1);
    expect(document.newerThanSupported).toBe(false);
    expect(document.people).toEqual([
      {
        id: "01KZQXWK571FJAF03F6H63A85Z",
        canonicalName: "Amina Hassan",
        aliases: ["amina.hassan@example.test"],
        summary: "Research partner.",
        isSelf: false,
        deleted: false,
      },
    ]);
  });

  it("ignores fields added by a newer server, as the compatibility promise allows", () => {
    const document = parsePersonIndex(
      indexText({
        pronouns: "they/them",
        people: [{ ...INDEX.people[0], future_field: { nested: true } }],
      }),
    );

    expect(document.people[0]?.canonicalName).toBe("Amina Hassan");
  });

  it("flags a newer document version rather than refusing it", () => {
    expect(parsePersonIndex(indexText({ version: 2 })).newerThanSupported).toBe(true);
  });

  it("rejects another document format", () => {
    expect(() => parsePersonIndex(briefText())).toThrowError(DocumentFormatError);
  });

  it("rejects unparseable output without quoting the payload", () => {
    const error = (() => {
      try {
        parsePersonIndex("Traceback: person Amina Hassan exploded");
        return null;
      } catch (caught) {
        return caught as Error;
      }
    })();

    expect(error).toBeInstanceOf(DocumentFormatError);
    expect(error?.message).not.toContain("Amina");
  });

  it("keeps an opaque id from a restored database, whatever it looks like", () => {
    // The sync-bundle identifier contract admits any non-blank string, so a restored store
    // can carry ids like these. Rejecting them would fail the entire index of a valid store.
    for (const id of ["person:alice", "--include-sensitive", "-rf", "urn:uuid:8f14e45f"]) {
      const document = parsePersonIndex(indexText({ people: [{ ...INDEX.people[0], id }] }));

      expect(document.people[0]?.id).toBe(id);
    }
  });

  it("rejects an entry whose id could not be an argument at all", () => {
    for (const id of ["", "   ", null, 7]) {
      expect(() =>
        parsePersonIndex(indexText({ people: [{ ...INDEX.people[0], id }] })),
      ).toThrowError(DocumentFormatError);
    }
  });

  it("rejects an entry with no usable name", () => {
    expect(() =>
      parsePersonIndex(indexText({ people: [{ ...INDEX.people[0], canonical_name: null }] })),
    ).toThrowError(DocumentFormatError);
  });
});

describe("parseBrief", () => {
  it("reads every section the pane renders", () => {
    const document = parseBrief(briefText());

    expect(document.person.canonicalName).toBe("Amina Hassan");
    expect(document.relationships).toEqual([
      { displayType: "colleague_of", otherPersonName: "Daniel Okafor", label: "since 2019" },
    ]);
    expect(document.affiliations).toEqual([
      { organizationName: "Civic Loom", role: "Researcher" },
    ]);
    expect(document.facts).toEqual([
      { predicate: "preferred_update_style", value: "Concise written updates" },
    ]);
    expect(document.interactions).toEqual([
      { summary: "Quarterly planning", occurredAt: "2026-06-10T09:00:00Z" },
    ]);
    expect(document.traits).toEqual([{ category: "communication_style", value: "Direct" }]);
    expect(document.reminders).toEqual([
      {
        kind: "follow_up",
        text: "Send the notes",
        dueAt: "2026-09-01T09:00:00Z",
        recurrence: null,
      },
    ]);
  });

  it("labels disclosure exactly as the document does", () => {
    const document = parseBrief(briefText());

    expect(document.disclosure).toEqual({
      includeSensitive: false,
      context: "ordinary",
      guidance: "ordinary",
      notice: "This brief is a local export.",
    });
    expect(document.guidance.disclosure).toBe("ordinary");
  });

  it("flattens guidance traits in a stable category order", () => {
    const document = parseBrief(briefText());

    expect(document.guidance.traits).toEqual([
      { category: "approach", value: "Structured" },
      { category: "tone", value: "Warm" },
    ]);
  });

  it("treats every collection as optional, because an empty person is still a person", () => {
    const document = parseBrief(
      JSON.stringify({
        format: "people-context-brief",
        version: 1,
        generated_at: "2026-08-11T08:09:18.333078Z",
        disclosure: {},
        person: { id: "01KZQXWK571FJAF03F6H63A85Z", canonical_name: "Solo" },
        guidance: {},
      }),
    );

    expect(document.relationships).toEqual([]);
    expect(document.reminders).toEqual([]);
    expect(document.guidance.traits).toEqual([]);
    expect(document.disclosure.context).toBe("ordinary");
  });

  it("keeps an opaque person id and rejects only an unusable one", () => {
    expect(parseBrief(briefText({ person: { ...BRIEF.person, id: "person:alice" } })).person.id).toBe(
      "person:alice",
    );
    expect(() => parseBrief(briefText({ person: { ...BRIEF.person, id: "  " } }))).toThrowError(
      DocumentFormatError,
    );
  });
});
