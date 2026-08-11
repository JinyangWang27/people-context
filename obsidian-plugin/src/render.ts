/**
 * Pure view models for the two panes.
 *
 * Rendering is split from the Obsidian layer so the interesting decisions — what is shown,
 * what is filtered, which id a click carries — are plain data that tests can assert on
 * without a running app. Every string here is display data: nothing in this module ever
 * becomes a command argument except an id that already passed `isSafePersonId`.
 */

import type {
  BriefDocument,
  BriefReminder,
  PersonIndexDocument,
  PersonIndexEntry,
} from "./documents.js";

export interface IndexRow {
  /** The stable id, and the only value that may be passed back to the CLI. */
  id: string;
  title: string;
  subtitle: string;
  isSelf: boolean;
}

export interface BriefSection {
  title: string;
  items: string[];
}

export interface BriefView {
  title: string;
  personId: string;
  headerLines: string[];
  notice: string;
  sections: BriefSection[];
  /** Set when the CLI document is newer than the version this plugin understands. */
  compatibilityWarning: string | null;
}

const NEWER_DOCUMENT_WARNING =
  "This people-context release emits a newer document version than the plugin was built " +
  "against. Known fields are shown; anything added since is not.";

/** Build the filtered, ordered rows for the index pane. */
export function buildIndexRows(document: PersonIndexDocument, query = ""): IndexRow[] {
  const needle = query.trim().toLowerCase();
  return document.people
    // The plugin never asks for soft-deleted people; this keeps them out even if a future
    // document carries them anyway.
    .filter((entry) => !entry.deleted)
    .filter((entry) => matches(entry, needle))
    .map((entry) => ({
      id: entry.id,
      title: entry.canonicalName,
      subtitle: subtitleFor(entry),
      isSelf: entry.isSelf,
    }));
}

function matches(entry: PersonIndexEntry, needle: string): boolean {
  if (needle === "") {
    return true;
  }
  const haystack = [entry.canonicalName, entry.summary ?? "", ...entry.aliases]
    .join("\n")
    .toLowerCase();
  return haystack.includes(needle);
}

function subtitleFor(entry: PersonIndexEntry): string {
  if (entry.summary !== null && entry.summary.trim() !== "") {
    return entry.summary;
  }
  return entry.aliases.join(", ");
}

/** Build the sectioned view model for one person's brief. */
export function buildBriefView(document: BriefDocument): BriefView {
  const person = document.person;
  const headerLines = [
    `Person id: ${person.id}`,
    `Generated: ${formatTimestamp(document.generatedAt)}`,
    `Self: ${person.isSelf ? "yes" : "no"}`,
    `Aliases: ${person.aliases.length > 0 ? person.aliases.join(", ") : "(none)"}`,
    `Summary: ${person.summary ?? "(none)"}`,
    `Context disclosure: ${document.disclosure.context}`,
    `Guidance disclosure: ${document.disclosure.guidance} (never widened)`,
  ];
  return {
    title: person.canonicalName,
    personId: person.id,
    headerLines,
    notice: document.disclosure.notice,
    compatibilityWarning: document.newerThanSupported ? NEWER_DOCUMENT_WARNING : null,
    sections: [
      {
        title: "Relationships",
        items: document.relationships.map((record) => {
          const base = `${record.displayType}: ${record.otherPersonName}`;
          return record.label === null ? base : `${base} — ${record.label}`;
        }),
      },
      {
        title: "Affiliations",
        items: document.affiliations.map((record) =>
          record.role === ""
            ? record.organizationName
            : `${record.role} at ${record.organizationName}`,
        ),
      },
      {
        title: "Facts",
        items: document.facts.map((fact) => `${fact.predicate}: ${fact.value}`),
      },
      {
        title: "Interactions",
        items: document.interactions.map(
          (interaction) => `${formatDate(interaction.occurredAt)}: ${interaction.summary}`,
        ),
      },
      {
        title: "Traits",
        items: document.traits.map((trait) => `${trait.category}: ${trait.value}`),
      },
      {
        title: "Reminders",
        items: document.reminders.map(reminderLine),
      },
      {
        title: `Communication guidance (${document.guidance.disclosure} disclosure)`,
        items: [
          `Philosophy: ${document.guidance.communicationPhilosophy ?? "(none set)"}`,
          ...document.guidance.traits.map((trait) => `${trait.category}: ${trait.value}`),
          ...document.guidance.frictionNotes.map((note) => `Recent note: ${note}`),
        ],
      },
    ],
  };
}

function reminderLine(reminder: BriefReminder): string {
  const due = reminder.dueAt === null ? "no due date" : `due ${formatTimestamp(reminder.dueAt)}`;
  const recurrence = reminder.recurrence === null ? "" : `, repeats ${reminder.recurrence}`;
  return `${reminder.kind} (${due}${recurrence}): ${reminder.text}`;
}

/**
 * Show a timestamp exactly as the document spelled it.
 *
 * Reformatting would mean parsing into a `Date` and back, which reads the host timezone and
 * makes the rendered pane depend on the machine that opened it. The stored spelling is
 * already deterministic, so it is shown unchanged.
 */
function formatTimestamp(value: string | null): string {
  return value === null || value.trim() === "" ? "(unknown)" : value;
}

/** The date component of an ISO timestamp, without interpreting it in any timezone. */
function formatDate(value: string | null): string {
  if (value === null) {
    return "(undated)";
  }
  return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : value;
}
