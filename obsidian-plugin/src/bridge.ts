/**
 * The subprocess bridge to the `pctx` command-line interface.
 *
 * Everything the plugin displays arrives through this module, and everything it displays is
 * untrusted personal data. The bridge therefore never builds a command string: it passes the
 * configured executable as the program and a pre-built array as the arguments, with
 * `shell: false`, so a name containing `$(...)`, backticks, `;`, `&`, `|`, `%VAR%`, or `^`
 * is inert text that a shell never sees.
 *
 * Every run is bounded. It has a finite timeout with process-tree termination, a hard cap on
 * captured stdout and stderr, and cooperative cancellation through an `AbortSignal`, so a
 * hung or runaway CLI cannot wedge or exhaust the Obsidian process.
 */

import { spawn as nodeSpawn } from "node:child_process";

import { DB_KEY_ENV } from "./settings.js";

/** Why a run failed. Callers branch on this rather than on message text. */
export type CliFailureKind =
  | "aborted"
  | "executable-not-found"
  | "exit"
  | "missing-key"
  | "oversized-output"
  | "spawn-failed"
  | "timeout";

export class PeopleContextCliError extends Error {
  readonly kind: CliFailureKind;
  /** A remedy the user can act on, when there is one. */
  readonly hint: string | undefined;

  constructor(kind: CliFailureKind, message: string, hint?: string) {
    super(message);
    this.name = "PeopleContextCliError";
    this.kind = kind;
    this.hint = hint;
  }
}

/** The minimal readable stream surface the bridge consumes. */
export interface ReadableStreamLike {
  on(event: "data", listener: (chunk: Buffer | string) => void): unknown;
}

/** The minimal child-process surface the bridge consumes. */
export interface ChildProcessLike {
  readonly pid?: number | undefined;
  readonly stdout: ReadableStreamLike | null;
  readonly stderr: ReadableStreamLike | null;
  on(event: "error", listener: (error: NodeJS.ErrnoException) => void): unknown;
  on(event: "close", listener: (code: number | null, signal: string | null) => void): unknown;
  kill(signal?: NodeJS.Signals | number): boolean;
}

/** Options the bridge passes to the injected spawner; deliberately a fixed, typed set. */
export interface SpawnOptionsLike {
  readonly cwd: string | undefined;
  readonly env: NodeJS.ProcessEnv;
  readonly shell: false;
  readonly windowsHide: true;
  readonly detached: boolean;
  readonly stdio: readonly ["ignore", "pipe", "pipe"];
}

/** The narrow spawn port. Production passes `child_process.spawn`; tests pass a fake. */
export type Spawner = (
  command: string,
  args: readonly string[],
  options: SpawnOptionsLike,
) => ChildProcessLike;

/** Terminates a run that overstayed its timeout, was cancelled, or overflowed its buffers. */
export type ProcessTerminator = (child: ChildProcessLike) => void;

export interface CliRunOptions {
  readonly executable: string;
  readonly args: readonly string[];
  readonly env: NodeJS.ProcessEnv;
  readonly timeoutMs: number;
  readonly maxOutputBytes: number;
  readonly signal?: AbortSignal | undefined;
  readonly cwd?: string | undefined;
  readonly spawn?: Spawner | undefined;
  readonly terminate?: ProcessTerminator | undefined;
}

/** Default bounds. A person index or one brief is small; anything larger is a fault. */
export const DEFAULT_TIMEOUT_MS = 20_000;
export const DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024;

/** How much stderr text is quoted back in a failure message. */
const STDERR_EXCERPT_LIMIT = 2_000;

/** The platform-dependent pieces of termination, injected so both branches are testable. */
export interface TerminationHooks {
  readonly platform: NodeJS.Platform;
  /** `process.kill`, so a test can observe the target and signal without a real process. */
  readonly kill: (pid: number, signal: NodeJS.Signals) => void;
  /** Used on Windows to run the tree-aware killer. */
  readonly spawn: Spawner;
}

/**
 * Build a terminator that kills the child *and* everything it started.
 *
 * A tree kill rather than a single signal is the requirement, because `pctx` is a console-script
 * launcher: on every platform the thing that actually holds the database may be a Python child
 * of the process this module spawned. Killing only the direct child would report a stopped run
 * while leaving that child alive.
 *
 * The two platforms need different mechanisms. POSIX has process groups, and the bridge spawns
 * detached precisely so one signal can reach the whole group. Windows has no process groups to
 * signal, so the tree is torn down with `taskkill /T /F`, which is spawned the same way as every
 * other command here — argument array, no shell.
 */
export function terminateWith(hooks: TerminationHooks): ProcessTerminator {
  return (child: ChildProcessLike): void => {
    const pid = child.pid;
    const directKill = (): void => {
      try {
        child.kill("SIGKILL");
      } catch {
        // The process already exited. Nothing to terminate.
      }
    };

    if (typeof pid !== "number" || pid <= 0) {
      directKill();
      return;
    }

    if (hooks.platform === "win32") {
      let killer: ChildProcessLike;
      try {
        killer = hooks.spawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
          cwd: undefined,
          env: {},
          shell: false,
          windowsHide: true,
          detached: false,
          stdio: ["ignore", "pipe", "pipe"],
        });
      } catch {
        // `spawn` only throws synchronously for a malformed invocation.
        directKill();
        return;
      }

      let fellBack = false;
      const fallBack = (): void => {
        if (fellBack) {
          return;
        }
        fellBack = true;
        directKill();
      };

      // Both listeners are required, not defensive extras. A `taskkill` that cannot be
      // launched — missing, or blocked by endpoint policy — is reported asynchronously as an
      // `error` event rather than thrown, and an `error` event with no listener on a child
      // process becomes an uncaught exception inside Obsidian. A non-zero exit means the tree
      // is still standing. Either way the direct child is the remaining option.
      killer.on("error", fallBack);
      killer.on("close", (code) => {
        if (code !== 0) {
          fallBack();
        }
      });
      return;
    }

    try {
      // Negative pid addresses the process group the detached child leads.
      hooks.kill(-pid, "SIGKILL");
    } catch {
      // The group is already gone, or was never created; fall through to the direct kill.
      directKill();
    }
  };
}

/** The terminator used in production, bound to the real platform. */
export const terminateProcessTree: ProcessTerminator = terminateWith({
  platform: process.platform,
  kill: (pid, signal) => {
    process.kill(pid, signal);
  },
  spawn: nodeSpawn as unknown as Spawner,
});

/**
 * Remove an inherited database key from text that is about to be shown or thrown.
 *
 * The plugin never puts the key in an argument or an environment it constructs, so this is a
 * backstop for text that came back from a child process, not the primary control.
 */
export function scrubSecrets(text: string, env: NodeJS.ProcessEnv): string {
  const key = env[DB_KEY_ENV];
  if (typeof key !== "string" || key.trim() === "") {
    return text;
  }
  return text.split(key).join("[redacted]");
}

function asBuffer(chunk: Buffer | string): Buffer {
  return typeof chunk === "string" ? Buffer.from(chunk, "utf8") : chunk;
}

/**
 * Join the captured chunks and decode once, at the end.
 *
 * Decoding chunk by chunk would corrupt any multibyte character the pipe happened to split:
 * each half would decode to a replacement character before the pieces were joined. The CLI
 * renders its JSON with `ensure_ascii=False`, so non-ASCII names, aliases, and summaries
 * travel as real UTF-8 bytes and are exactly what such a split would damage. Concatenating
 * first makes the chunk boundaries invisible.
 */
function asText(chunks: readonly Buffer[]): string {
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Run one `pctx` invocation and return its stdout.
 *
 * The promise settles exactly once: the first terminal condition wins and every later event
 * is ignored, so a process that is killed on timeout cannot also report a non-zero exit.
 */
export function runCli(options: CliRunOptions): Promise<string> {
  const spawner = options.spawn ?? (nodeSpawn as unknown as Spawner);
  const terminate = options.terminate ?? terminateProcessTree;

  return new Promise<string>((resolve, reject) => {
    if (options.signal?.aborted === true) {
      reject(new PeopleContextCliError("aborted", "The people-context request was cancelled."));
      return;
    }

    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abortListener: (() => void) | undefined;
    // Captured as buffers, decoded only once both streams are complete.
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;

    const cleanup = (): void => {
      if (timer !== undefined) {
        clearTimeout(timer);
        timer = undefined;
      }
      if (abortListener !== undefined) {
        options.signal?.removeEventListener("abort", abortListener);
        abortListener = undefined;
      }
    };

    const succeed = (value: string): void => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };

    const fail = (error: PeopleContextCliError): void => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };

    let child: ChildProcessLike;
    try {
      child = spawner(options.executable, options.args, {
        cwd: options.cwd,
        env: options.env,
        // The three controls that make untrusted display data safe as arguments.
        shell: false,
        windowsHide: true,
        // A POSIX process group exists so the timeout can terminate the whole tree.
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      fail(
        new PeopleContextCliError(
          "spawn-failed",
          `Could not start "${options.executable}": ${scrubSecrets(describe(error), options.env)}`,
          "Check the people-context executable path in the plugin settings.",
        ),
      );
      return;
    }

    const overflow = (stream: "stdout" | "stderr"): void => {
      terminate(child);
      fail(
        new PeopleContextCliError(
          "oversized-output",
          `The people-context command produced more than ${options.maxOutputBytes} bytes on ${stream} and was stopped.`,
          "Narrow the request, or check that the configured executable is really pctx.",
        ),
      );
    };

    child.stdout?.on("data", (chunk) => {
      if (settled) return;
      const buffer = asBuffer(chunk);
      stdoutBytes += buffer.length;
      if (stdoutBytes > options.maxOutputBytes) {
        // The partial payload is dropped rather than kept: it is personal data that no
        // longer parses, and holding it only risks it reaching a message or a log.
        stdout.length = 0;
        overflow("stdout");
        return;
      }
      stdout.push(buffer);
    });

    child.stderr?.on("data", (chunk) => {
      if (settled) return;
      const buffer = asBuffer(chunk);
      stderrBytes += buffer.length;
      if (stderrBytes > options.maxOutputBytes) {
        stderr.length = 0;
        overflow("stderr");
        return;
      }
      stderr.push(buffer);
    });

    child.on("error", (error) => {
      if (error.code === "ENOENT") {
        fail(
          new PeopleContextCliError(
            "executable-not-found",
            `The people-context executable "${options.executable}" was not found.`,
            "Set the full path to pctx in the plugin settings, for example the one printed by `which pctx`.",
          ),
        );
        return;
      }
      fail(
        new PeopleContextCliError(
          "spawn-failed",
          `The people-context command failed to run: ${scrubSecrets(describe(error), options.env)}`,
          "Check the people-context executable path in the plugin settings.",
        ),
      );
    });

    child.on("close", (code, signal) => {
      if (code === 0) {
        succeed(asText(stdout));
        return;
      }
      const detail = scrubSecrets(asText(stderr), options.env).trim().slice(0, STDERR_EXCERPT_LIMIT);
      const status = code === null ? `signal ${signal ?? "unknown"}` : `exit code ${code}`;
      fail(
        new PeopleContextCliError(
          "exit",
          detail === ""
            ? `The people-context command ended with ${status}.`
            : `The people-context command ended with ${status}: ${detail}`,
        ),
      );
    });

    if (options.timeoutMs > 0) {
      timer = setTimeout(() => {
        terminate(child);
        fail(
          new PeopleContextCliError(
            "timeout",
            `The people-context command did not finish within ${options.timeoutMs} ms and was stopped.`,
          ),
        );
      }, options.timeoutMs);
    }

    if (options.signal !== undefined) {
      abortListener = (): void => {
        terminate(child);
        fail(new PeopleContextCliError("aborted", "The people-context request was cancelled."));
      };
      options.signal.addEventListener("abort", abortListener, { once: true });
    }
  });
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
