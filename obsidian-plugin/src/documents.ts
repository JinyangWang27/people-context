/**
 * Parsing for the two versioned CLI documents the plugin consumes.
 *
 * `people-context-person-index` and `people-context-brief` are declared machine interfaces
 * under the project's compatibility promise: fields are added, never removed or repurposed.
 * The parsers here match that promise exactly — unknown fields are ignored rather than
 * rejected, so a newer server keeps working, while the fields the plugin actually renders are
 * checked before use rather than assumed.
 *
 * Nothing here trusts the payload. Every string is read as display data; only the person id
 * is ever allowed to become a command argument, and even then it travels after a `--`
 * separator rather than being trusted to look harmless.
 */

import { isUsablePersonId } from "./settings.js";

export const PERSON_INDEX_FORMAT = "people-context-person-index";
export const PERSON_INDEX_VERSION = 1;
export const BRIEF_FORMAT = "people-context-brief";
export const BRIEF_VERSION = 1;

export class DocumentFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DocumentFormatError";
  }
}

export interface PersonIndexEntry {
  id: string;
  canonicalName: string;
  aliases: string[];
  summary: string | null;
  isSelf: boolean;
  deleted: boolean;
}

export interface PersonIndexDocument {
  version: number;
  generatedAt: string | null;
  includeDeleted: boolean;
  people: PersonIndexEntry[];
  /** True when the CLI is newer than the version this plugin was written against. */
  newerThanSupported: boolean;
}

export interface BriefRelationship {
  displayType: string;
  otherPersonName: string;
  label: string | null;
}

export interface BriefAffiliation {
  organizationName: string;
  role: string;
}

export interface BriefFact {
  predicate: string;
  value: string;
}

export interface BriefInteraction {
  occurredAt: string | null;
  summary: string;
}

export interface BriefTrait {
  category: string;
  value: string;
}

export interface BriefReminder {
  kind: string;
  text: string;
  dueAt: string | null;
  recurrence: string | null;
}

export interface BriefGuidance {
  disclosure: string;
  traits: BriefTrait[];
  frictionNotes: string[];
  communicationPhilosophy: string | null;
}

export interface BriefDisclosure {
  includeSensitive: boolean;
  context: string;
  guidance: string;
  notice: string;
}

export interface BriefDocument {
  version: number;
  generatedAt: string | null;
  disclosure: BriefDisclosure;
  person: PersonIndexEntry;
  relationships: BriefRelationship[];
  affiliations: BriefAffiliation[];
  facts: BriefFact[];
  interactions: BriefInteraction[];
  traits: BriefTrait[];
  reminders: BriefReminder[];
  guidance: BriefGuidance;
  newerThanSupported: boolean;
}

/** Parse the `pctx list --json` document. */
export function parsePersonIndex(text: string): PersonIndexDocument {
  const root = parseEnvelope(text, PERSON_INDEX_FORMAT);
  const people = readArray(root.value.people).map((entry, index) =>
    parseIndexEntry(entry, `people[${index}]`),
  );
  return {
    version: root.version,
    generatedAt: readOptionalString(root.value.generated_at),
    includeDeleted: root.value.include_deleted === true,
    people,
    newerThanSupported: root.version > PERSON_INDEX_VERSION,
  };
}

/** Parse the `pctx brief <id> --json` document. */
export function parseBrief(text: string): BriefDocument {
  const root = parseEnvelope(text, BRIEF_FORMAT);
  const value = root.value;
  const person = parseIndexEntry(value.person, "person");
  const disclosure = readObject(value.disclosure);
  const guidance = readObject(value.guidance);
  return {
    version: root.version,
    generatedAt: readOptionalString(value.generated_at),
    disclosure: {
      includeSensitive: disclosure.include_sensitive === true,
      context: readOptionalString(disclosure.context) ?? "ordinary",
      guidance: readOptionalString(disclosure.guidance) ?? "ordinary",
      notice: readOptionalString(disclosure.notice) ?? "",
    },
    person,
    relationships: readArray(value.relationships).map(parseRelationship),
    affiliations: readArray(value.affiliations).map(parseAffiliation),
    facts: readArray(value.facts).map(parseFact),
    interactions: readArray(value.interactions).map(parseInteraction),
    traits: readArray(value.traits).map(parseTrait),
    reminders: readArray(value.reminders).map(parseReminder),
    guidance: {
      disclosure: readOptionalString(guidance.disclosure) ?? "ordinary",
      traits: parseGuidanceTraits(guidance.traits),
      frictionNotes: readArray(guidance.friction_notes)
        .map((note) => readOptionalString(note))
        .filter((note): note is string => note !== null),
      communicationPhilosophy: readOptionalString(guidance.communication_philosophy),
    },
    newerThanSupported: root.version > BRIEF_VERSION,
  };
}

interface Envelope {
  value: Record<string, unknown>;
  version: number;
}

function parseEnvelope(text: string, expectedFormat: string): Envelope {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text);
  } catch {
    // The payload itself is never quoted back: it is personal data.
    throw new DocumentFormatError("The people-context command did not return valid JSON.");
  }
  if (typeof decoded !== "object" || decoded === null || Array.isArray(decoded)) {
    throw new DocumentFormatError("The people-context command did not return a JSON object.");
  }
  const value = decoded as Record<string, unknown>;
  if (value.format !== expectedFormat) {
    throw new DocumentFormatError(
      `Expected a "${expectedFormat}" document from the people-context CLI.`,
    );
  }
  const version = value.version;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) {
    throw new DocumentFormatError(`The "${expectedFormat}" document has no usable version.`);
  }
  return { value, version };
}

function parseIndexEntry(raw: unknown, path: string): PersonIndexEntry {
  const entry = readObject(raw);
  const id = entry.id;
  if (!isUsablePersonId(id)) {
    throw new DocumentFormatError(`The people-context document has an unusable id at ${path}.`);
  }
  const canonicalName = readOptionalString(entry.canonical_name);
  if (canonicalName === null) {
    throw new DocumentFormatError(`The people-context document has no name at ${path}.`);
  }
  return {
    id,
    canonicalName,
    aliases: readArray(entry.aliases)
      .map((alias) => readOptionalString(alias))
      .filter((alias): alias is string => alias !== null),
    summary: readOptionalString(entry.summary),
    isSelf: entry.is_self === true,
    deleted: entry.deleted === true,
  };
}

function parseRelationship(raw: unknown): BriefRelationship {
  const record = readObject(raw);
  const relationship = readObject(record.relationship);
  return {
    displayType: readOptionalString(record.display_type) ?? "related to",
    otherPersonName: readOptionalString(record.other_person_name) ?? "(unnamed)",
    label: readOptionalString(relationship.label),
  };
}

function parseAffiliation(raw: unknown): BriefAffiliation {
  const record = readObject(raw);
  const affiliation = readObject(record.affiliation);
  return {
    organizationName: readOptionalString(record.organization_name) ?? "(unnamed organization)",
    role: readOptionalString(affiliation.role) ?? "",
  };
}

function parseFact(raw: unknown): BriefFact {
  const record = readObject(raw);
  return {
    predicate: readOptionalString(record.predicate) ?? "",
    value: readOptionalString(record.value) ?? "",
  };
}

function parseInteraction(raw: unknown): BriefInteraction {
  const record = readObject(raw);
  return {
    occurredAt: readOptionalString(record.occurred_at),
    summary: readOptionalString(record.summary) ?? "",
  };
}

function parseTrait(raw: unknown): BriefTrait {
  const record = readObject(raw);
  return {
    category: readOptionalString(record.category) ?? "",
    value: readOptionalString(record.value) ?? "",
  };
}

function parseReminder(raw: unknown): BriefReminder {
  const record = readObject(raw);
  return {
    kind: readOptionalString(record.kind) ?? "",
    text: readOptionalString(record.text) ?? "",
    dueAt: readOptionalString(record.due_at),
    recurrence: readOptionalString(record.recurrence),
  };
}

/**
 * Flatten the guidance trait map into a stable list.
 *
 * The CLI keys this object by category and the plugin renders a flat list, so the categories
 * are sorted here rather than left to whatever order `JSON.parse` produced.
 */
function parseGuidanceTraits(raw: unknown): BriefTrait[] {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return [];
  }
  const traits: BriefTrait[] = [];
  for (const category of Object.keys(raw as Record<string, unknown>).sort()) {
    for (const entry of readArray((raw as Record<string, unknown>)[category])) {
      const parsed = parseTrait(entry);
      traits.push({ category, value: parsed.value });
    }
  }
  return traits;
}

function readObject(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new DocumentFormatError("The people-context document has an unexpected shape.");
  }
  return raw as Record<string, unknown>;
}

function readArray(raw: unknown): unknown[] {
  return Array.isArray(raw) ? raw : [];
}

function readOptionalString(raw: unknown): string | null {
  return typeof raw === "string" ? raw : null;
}
