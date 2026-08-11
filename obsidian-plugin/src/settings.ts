/**
 * Typed plugin settings and the argument arrays built from them.
 *
 * Settings are deliberately a fixed set of typed fields rather than a free-form command
 * string: there is no place for a user (or for imported contact data) to inject an extra
 * argument, a redirection, or a shell fragment. Every invocation is built here as an array,
 * and the executable is passed to `spawn` as the program, never interpolated into text.
 */

/** When the plugin refreshes a pane without being asked. */
export type RefreshPolicy = "manual" | "on-open";

export const REFRESH_POLICIES: readonly RefreshPolicy[] = ["manual", "on-open"];

/** The complete, typed persisted settings of the plugin. */
export interface PeopleContextSettings {
  /** Path to the `pctx` executable, or a bare name resolved through `PATH`. */
  executablePath: string;
  /** Optional explicit database path; empty means "let the CLI resolve it". */
  databasePath: string;
  /** Open the database with SQLCipher, using the inherited key. */
  encryptedDatabase: boolean;
  /** Whether opening a pane refreshes it automatically. */
  refreshPolicy: RefreshPolicy;
}

export const DEFAULT_SETTINGS: PeopleContextSettings = {
  executablePath: "pctx",
  databasePath: "",
  encryptedDatabase: false,
  // Resolves M14 open question 4: a pane that opens empty and needs a second manual action
  // reads as broken, and one read is cheap, so opening refreshes. Nothing polls on a timer.
  refreshPolicy: "on-open",
};

/** The environment variable the CLI reads an encryption key from. It is never stored here. */
export const DB_KEY_ENV = "PEOPLE_CONTEXT_DB_KEY";

/**
 * Coerce arbitrary persisted JSON into the typed settings shape.
 *
 * Obsidian hands back whatever is on disk, including data written by an older or hand-edited
 * version, so every field is validated rather than trusted.
 */
export function normalizeSettings(raw: unknown): PeopleContextSettings {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const executable = typeof source.executablePath === "string" ? source.executablePath.trim() : "";
  const database = typeof source.databasePath === "string" ? source.databasePath.trim() : "";
  const refresh = source.refreshPolicy;
  return {
    executablePath: executable === "" ? DEFAULT_SETTINGS.executablePath : executable,
    databasePath: database,
    encryptedDatabase: source.encryptedDatabase === true,
    refreshPolicy: isRefreshPolicy(refresh) ? refresh : DEFAULT_SETTINGS.refreshPolicy,
  };
}

export function isRefreshPolicy(value: unknown): value is RefreshPolicy {
  return typeof value === "string" && (REFRESH_POLICIES as readonly string[]).includes(value);
}

/**
 * Build the fixed global-argument prefix: optional `--db <path>`, then optional `--encrypted`.
 *
 * The database path travels as its own array element, so a path containing spaces, quotes, or
 * shell metacharacters is one literal argument and nothing else.
 */
export function globalArguments(settings: PeopleContextSettings): string[] {
  const args: string[] = [];
  if (settings.databasePath !== "") {
    args.push("--db", settings.databasePath);
  }
  if (settings.encryptedDatabase) {
    args.push("--encrypted");
  }
  return args;
}

/** Build a complete argument array for one subcommand invocation. */
export function commandArguments(
  settings: PeopleContextSettings,
  subcommand: readonly string[],
): string[] {
  return [...globalArguments(settings), ...subcommand];
}

/** The argument array for the person index. */
export function listArguments(settings: PeopleContextSettings): string[] {
  // `--all` is never passed: a soft-deleted person is not something to browse.
  // `--include-sensitive` has no place on this command and does not exist for it.
  return commandArguments(settings, ["list", "--json"]);
}

/**
 * The argument array for one person's brief, addressed by stable id.
 *
 * `pctx brief` also accepts a name, and the plugin never uses that form: a display name is
 * untrusted contact data, and resolving it could silently address a different person.
 * `--include-sensitive` is never passed, so a synced vault only ever sees ordinary disclosure.
 *
 * The id goes last, after a bare `--`. That separator is what makes an opaque id safe: the
 * project's identifier contract admits any non-blank string, so an id may legitimately begin
 * with `-`, and without the separator the CLI's own parser would read it as an option rather
 * than as the person to brief. Every real option is therefore emitted before the `--`.
 */
export function briefArguments(settings: PeopleContextSettings, personId: string): string[] {
  assertUsablePersonId(personId);
  return commandArguments(settings, ["brief", "--json", "--", personId]);
}

/**
 * Whether an id can be used at all.
 *
 * Deliberately not a grammar. Ids are opaque: a database restored from a sync bundle may
 * carry any non-blank identifier, and a plugin that insisted on ULID-shaped ids would reject
 * the whole index of a perfectly valid store. Safety comes from argument separation, not from
 * narrowing the data contract. The only rejections are values that cannot be an argument at
 * all — a blank id, and one containing a NUL byte, which `spawn` refuses outright.
 */
export function isUsablePersonId(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "" && !value.includes("\0");
}

export function assertUsablePersonId(value: string): void {
  if (!isUsablePersonId(value)) {
    // The rejected value is not echoed: it reached us from the database and may be
    // personal data. The caller only needs to know the id was unusable.
    throw new Error("Refusing to run a command for an unusable person id.");
  }
}
