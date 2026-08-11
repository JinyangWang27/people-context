/**
 * The read-only people-context client: settings in, parsed documents out.
 *
 * This is the only place the plugin decides *what* to ask the CLI for. It asks for exactly
 * two things — the person index and one person's brief — and it addresses the brief by the
 * stable id the index returned, never by a display name. It never passes
 * `--include-sensitive`, so everything that can reach a synced vault stays at ordinary
 * disclosure.
 */

import {
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_TIMEOUT_MS,
  PeopleContextCliError,
  type ProcessTerminator,
  type Spawner,
  runCli,
} from "./bridge.js";
import { type BriefDocument, type PersonIndexDocument, parseBrief, parsePersonIndex } from "./documents.js";
import { DB_KEY_ENV, type PeopleContextSettings, briefArguments, listArguments } from "./settings.js";

/**
 * The CLI's own refusal, quoted verbatim.
 *
 * The plugin checks for the key before spawning so the user sees the real reason instead of a
 * bare non-zero exit, and the wording is the CLI's so that the two surfaces cannot drift into
 * describing the same refusal differently.
 */
export const MISSING_KEY_MESSAGE =
  "Encrypted mode requires a non-empty PEOPLE_CONTEXT_DB_KEY environment variable. " +
  "Refusing to continue; plaintext is never used as a fallback.";

export const MISSING_KEY_HINT =
  `Obsidian did not inherit ${DB_KEY_ENV}. Launch Obsidian from a shell that exports the key, ` +
  "or set it in the desktop session your launcher uses, then reload the plugin. " +
  "The plugin never stores or prompts for the key, and never opens the database unencrypted instead.";

/** Raised when a brief came back describing someone other than the person that was asked for. */
export class PersonIdentityError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PersonIdentityError";
  }
}

export interface PeopleContextClientOptions {
  /** Read the live settings; the client is constructed once and settings change under it. */
  readonly settings: () => PeopleContextSettings;
  readonly env: NodeJS.ProcessEnv;
  readonly timeoutMs?: number | undefined;
  readonly maxOutputBytes?: number | undefined;
  readonly cwd?: string | undefined;
  readonly spawn?: Spawner | undefined;
  readonly terminate?: ProcessTerminator | undefined;
}

export class PeopleContextClient {
  private readonly options: PeopleContextClientOptions;

  constructor(options: PeopleContextClientOptions) {
    this.options = options;
  }

  /** Read the person index. */
  async listPeople(signal?: AbortSignal): Promise<PersonIndexDocument> {
    const settings = this.options.settings();
    return parsePersonIndex(await this.run(listArguments(settings), settings, signal));
  }

  /**
   * Read one person's brief, addressed by the stable id from the index.
   *
   * The returned document is checked against the id that was asked for. `pctx brief` takes an
   * id *or* a name: it looks the reference up as an id first and, failing that, resolves it as
   * a name. So a row that went stale — its person deleted or merged away between the index read
   * and the click — can come back as a *different*, still-active person whose name happens to
   * match the old identifier. Identifiers are opaque and may legitimately be name-shaped, so
   * that is a reachable path, and silently rendering the wrong person's records under the
   * requested person's heading is the one failure this pane must never have.
   */
  async getBrief(personId: string, signal?: AbortSignal): Promise<BriefDocument> {
    const settings = this.options.settings();
    const document = parseBrief(await this.run(briefArguments(settings, personId), settings, signal));
    if (document.person.id !== personId) {
      // Neither id is echoed: both are database values, and the user only needs to know the
      // row is stale rather than which record answered.
      throw new PersonIdentityError(
        "That person is no longer available under the id the list returned. Refresh the people list.",
      );
    }
    return document;
  }

  private async run(
    args: readonly string[],
    settings: PeopleContextSettings,
    signal: AbortSignal | undefined,
  ): Promise<string> {
    this.requireKeyForEncryptedMode(settings);
    return runCli({
      executable: settings.executablePath,
      args,
      // The child inherits the Obsidian environment as-is. That inheritance is how an
      // encrypted database gets its key: the plugin never reads, copies, stores, or logs it.
      env: this.options.env,
      timeoutMs: this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      maxOutputBytes: this.options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES,
      signal,
      cwd: this.options.cwd,
      spawn: this.options.spawn,
      terminate: this.options.terminate,
    });
  }

  /** Refuse an encrypted run with no inherited key, rather than silently reading plaintext. */
  private requireKeyForEncryptedMode(settings: PeopleContextSettings): void {
    if (!settings.encryptedDatabase) {
      return;
    }
    const key = this.options.env[DB_KEY_ENV];
    if (typeof key !== "string" || key.trim() === "") {
      throw new PeopleContextCliError("missing-key", MISSING_KEY_MESSAGE, MISSING_KEY_HINT);
    }
  }
}
