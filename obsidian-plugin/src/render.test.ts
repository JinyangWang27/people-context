import { describe, expect, it } from "vitest";

import type { BriefDocument, PersonIndexDocument, PersonIndexEntry } from "./documents.js";
import { IndexPaneModel, buildBriefView, buildIndexRows } from "./render.js";

function entry(overrides: Partial<PersonIndexEntry> = {}): PersonIndexEntry {
  return {
    id: "01KZQXWK571FJAF03F6H63A85Z",
    canonicalName: "Amina Hassan",
    aliases: ["amina.hassan@example.test"],
    summary: "Research partner.",
    isSelf: false,
    deleted: false,
    ...overrides,
  };
}

function index(people: PersonIndexEntry[]): PersonIndexDocument {
  return {
    version: 1,
    generatedAt: "2026-08-11T08:09:14.776653Z",
    includeDeleted: false,
    people,
    newerThanSupported: false,
  };
}

function brief(overrides: Partial<BriefDocument> = {}): BriefDocument {
  return {
    version: 1,
    generatedAt: "2026-08-11T08:09:18.333078Z",
    disclosure: {
      includeSensitive: false,
      context: "ordinary",
      guidance: "ordinary",
      notice: "This brief is a local export.",
    },
    person: entry(),
    relationships: [],
    affiliations: [],
    facts: [],
    interactions: [],
    traits: [],
    reminders: [],
    guidance: {
      disclosure: "ordinary",
      traits: [],
      frictionNotes: [],
      communicationPhilosophy: null,
    },
    newerThanSupported: false,
    ...overrides,
  };
}

describe("buildIndexRows", () => {
  it("keeps the document order and carries the stable id, not the name", () => {
    const rows = buildIndexRows(
      index([entry(), entry({ id: "01KZQXWK58QY1T6M8CETCXZVM0", canonicalName: "Daniel Okafor" })]),
    );

    expect(rows.map((row) => row.id)).toEqual([
      "01KZQXWK571FJAF03F6H63A85Z",
      "01KZQXWK58QY1T6M8CETCXZVM0",
    ]);
  });

  it("filters case-insensitively across name, summary, and aliases", () => {
    const document = index([
      entry(),
      entry({
        id: "01KZQXWK58QY1T6M8CETCXZVM0",
        canonicalName: "Daniel Okafor",
        aliases: ["dokafor@example.test"],
        summary: "Engineering manager.",
      }),
    ]);

    expect(buildIndexRows(document, "AMINA").map((row) => row.title)).toEqual(["Amina Hassan"]);
    expect(buildIndexRows(document, "engineering").map((row) => row.title)).toEqual([
      "Daniel Okafor",
    ]);
    expect(buildIndexRows(document, "example.test")).toHaveLength(2);
    expect(buildIndexRows(document, "  ")).toHaveLength(2);
  });

  it("keeps a soft-deleted person out even if a document carries one", () => {
    expect(buildIndexRows(index([entry({ deleted: true })]))).toEqual([]);
  });

  it("falls back to aliases when there is no summary", () => {
    const rows = buildIndexRows(index([entry({ summary: null })]));

    expect(rows[0]?.subtitle).toBe("amina.hassan@example.test");
  });

  it("treats a hostile name as inert display text and still routes by id", () => {
    const hostile = "Bobby; rm -rf ~ $(whoami) `id` & | %USERPROFILE% ^x";
    const rows = buildIndexRows(index([entry({ canonicalName: hostile, summary: null })]));

    expect(rows[0]?.title).toBe(hostile);
    expect(rows[0]?.id).toBe("01KZQXWK571FJAF03F6H63A85Z");
  });
});

describe("IndexPaneModel", () => {
  const document = () =>
    index([
      entry(),
      entry({
        id: "01KZQXWK58QY1T6M8CETCXZVM0",
        canonicalName: "Daniel Okafor",
        aliases: ["dokafor@example.test"],
        summary: "Engineering manager.",
      }),
    ]);

  it("filters on a query change alone, with no new read", () => {
    const model = new IndexPaneModel();
    model.setDocument(document());

    expect(model.rows()).toHaveLength(2);

    model.setQuery("daniel");

    // Regression: the rows are derived on demand, so typing filters without another CLI call.
    expect(model.rows().map((row) => row.title)).toEqual(["Daniel Okafor"]);
    expect(model.status()).toBe("1 of 2 shown.");
  });

  it("restores every row when the query is cleared", () => {
    const model = new IndexPaneModel();
    model.setDocument(document());
    model.setQuery("daniel");
    model.setQuery("");

    expect(model.rows()).toHaveLength(2);
    expect(model.status()).toBe("2 of 2 shown.");
  });

  it("keeps the query across a refresh", () => {
    const model = new IndexPaneModel();
    model.setQuery("daniel");
    model.beginRead();
    model.setDocument(document());

    expect(model.rows().map((row) => row.title)).toEqual(["Daniel Okafor"]);
  });

  it("drops a previous failure as soon as a read starts", () => {
    const model = new IndexPaneModel();
    model.setFailure(new Error("pctx not found"));
    model.beginRead();

    expect(model.error()).toBeNull();
    expect(model.status()).toBe("Reading…");
  });

  it("drops a previous failure when a read succeeds", () => {
    const model = new IndexPaneModel();
    model.setFailure(new Error("pctx not found"));
    model.setDocument(document());

    // Regression: a fixed configuration must not leave the old error beside the new list.
    expect(model.error()).toBeNull();
    expect(model.rows()).toHaveLength(2);
    expect(model.status()).toBe("2 of 2 shown.");
  });

  it("replaces a failure rather than accumulating failures", () => {
    const model = new IndexPaneModel();
    const second = new Error("database is locked");
    model.setFailure(new Error("pctx not found"));
    model.setFailure(second);

    expect(model.error()).toBe(second);
    expect(model.rows()).toEqual([]);
    expect(model.status()).toBe("");
  });

  it("describes the states a pane can be in before anything is loaded", () => {
    const model = new IndexPaneModel();

    expect(model.rows()).toEqual([]);
    expect(model.error()).toBeNull();
    expect(model.status()).toBe("Select Refresh to read the people-context database.");
  });

  it("reports an empty store distinctly from an empty filter result", () => {
    const model = new IndexPaneModel();
    model.setDocument(index([]));
    expect(model.status()).toBe("No people recorded yet.");

    model.setDocument(document());
    model.setQuery("nobody matches this");
    expect(model.status()).toBe("0 of 2 shown.");
  });
});

describe("buildBriefView", () => {
  it("labels both disclosure levels and states that guidance is never widened", () => {
    const view = buildBriefView(brief());

    expect(view.headerLines).toContain("Context disclosure: ordinary");
    expect(view.headerLines).toContain("Guidance disclosure: ordinary (never widened)");
    expect(view.notice).toBe("This brief is a local export.");
  });

  it("emits every section, keeping an empty one visible", () => {
    const view = buildBriefView(brief());

    expect(view.sections.map((section) => section.title)).toEqual([
      "Relationships",
      "Affiliations",
      "Facts",
      "Interactions",
      "Traits",
      "Reminders",
      "Communication guidance (ordinary disclosure)",
    ]);
    expect(view.sections[0]?.items).toEqual([]);
  });

  it("renders each record type in its documented shape", () => {
    const view = buildBriefView(
      brief({
        relationships: [
          { displayType: "colleague_of", otherPersonName: "Daniel Okafor", label: "since 2019" },
          { displayType: "friend_of", otherPersonName: "Sofia Alvarez", label: null },
        ],
        affiliations: [{ organizationName: "Civic Loom", role: "Researcher" }],
        facts: [{ predicate: "role", value: "Lead" }],
        interactions: [{ occurredAt: "2026-06-10T09:00:00Z", summary: "Quarterly planning" }],
        reminders: [
          { kind: "follow_up", text: "Send notes", dueAt: null, recurrence: "yearly" },
          {
            kind: "occasion",
            text: "Birthday",
            dueAt: "2026-09-01T09:00:00Z",
            recurrence: null,
          },
        ],
      }),
    );
    const items = (title: string): string[] =>
      view.sections.find((section) => section.title === title)?.items ?? [];

    expect(items("Relationships")).toEqual([
      "colleague_of: Daniel Okafor — since 2019",
      "friend_of: Sofia Alvarez",
    ]);
    expect(items("Affiliations")).toEqual(["Researcher at Civic Loom"]);
    expect(items("Facts")).toEqual(["role: Lead"]);
    expect(items("Interactions")).toEqual(["2026-06-10: Quarterly planning"]);
    expect(items("Reminders")).toEqual([
      "follow_up (no due date, repeats yearly): Send notes",
      "occasion (due 2026-09-01T09:00:00Z): Birthday",
    ]);
  });

  it("shows timestamps exactly as stored, so the pane does not depend on the host timezone", () => {
    const view = buildBriefView(brief());

    expect(view.headerLines).toContain("Generated: 2026-08-11T08:09:18.333078Z");
  });

  it("warns, rather than failing, when the CLI document is newer", () => {
    expect(buildBriefView(brief({ newerThanSupported: true })).compatibilityWarning).toContain(
      "newer document version",
    );
    expect(buildBriefView(brief()).compatibilityWarning).toBeNull();
  });

  it("puts guidance philosophy, traits, and notes in the ordinary-disclosure section", () => {
    const view = buildBriefView(
      brief({
        guidance: {
          disclosure: "ordinary",
          traits: [{ category: "tone", value: "Warm" }],
          frictionNotes: ["Quarterly planning"],
          communicationPhilosophy: "Be brief.",
        },
      }),
    );

    expect(view.sections.at(-1)?.items).toEqual([
      "Philosophy: Be brief.",
      "tone: Warm",
      "Recent note: Quarterly planning",
    ]);
  });
});
