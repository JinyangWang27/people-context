import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_TIMEOUT_MS,
  PeopleContextCliError,
  runCli,
  scrubSecrets,
} from "./bridge.js";
import { recordingSpawner } from "./testing/fake-process.js";

/** Arguments a shell would mangle, and which must survive verbatim as array elements. */
const HOSTILE_ARGUMENTS = [
  "Bobby; rm -rf ~",
  "$(whoami)",
  "`id`",
  "a & b",
  "a | b",
  "a > out.txt",
  "a < in.txt",
  "50% of $HOME",
  "^caret^",
  "%USERPROFILE%",
  '"double quoted"',
  "'single quoted'",
  "back\\slash",
  "new\nline",
];

/** Small real programs the bridge is driven against, instead of an inline `-e` expression. */
function fixture(name: string): string {
  return fileURLToPath(new URL(`./testing/${name}`, import.meta.url));
}

function realRun(args: readonly string[], overrides: Partial<Parameters<typeof runCli>[0]> = {}) {
  return runCli({
    executable: process.execPath,
    args,
    env: process.env,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    maxOutputBytes: DEFAULT_MAX_OUTPUT_BYTES,
    ...overrides,
  });
}

describe("spawn options", () => {
  it("never uses a shell, hides the console window, and pipes both streams", async () => {
    const spawner = recordingSpawner((child) => {
      child.emitClose(0);
    });

    await runCli({
      executable: "/opt/people-context/bin/pctx",
      args: ["list", "--json"],
      env: { PATH: "/usr/bin" },
      timeoutMs: 1_000,
      maxOutputBytes: 1_000,
      spawn: spawner.spawn,
    });

    const call = spawner.only();
    expect(call.command).toBe("/opt/people-context/bin/pctx");
    expect(call.args).toEqual(["list", "--json"]);
    expect(call.options.shell).toBe(false);
    expect(call.options.windowsHide).toBe(true);
    expect(call.options.stdio).toEqual(["ignore", "pipe", "pipe"]);
    expect(call.options.env).toEqual({ PATH: "/usr/bin" });
  });

  it("passes the executable as the program rather than as text", async () => {
    const executable = "/home/a b/pctx; rm -rf ~";
    const spawner = recordingSpawner((child) => {
      child.emitClose(0);
    });

    await runCli({
      executable,
      args: [],
      env: {},
      timeoutMs: 1_000,
      maxOutputBytes: 1_000,
      spawn: spawner.spawn,
    });

    expect(spawner.only().command).toBe(executable);
  });
});

describe("argument handling against a real process", () => {
  it("delivers shell metacharacters to the child verbatim", async () => {
    const stdout = await realRun([fixture("echo-argv.mjs"), ...HOSTILE_ARGUMENTS]);

    expect(JSON.parse(stdout)).toEqual(HOSTILE_ARGUMENTS);
  });

  it("keeps a database path containing spaces and metacharacters as one argument", async () => {
    const path = "/home/a b/People's \"data\"; $(pwd) `x` & | %HOME% ^db.sqlite";

    const stdout = await realRun([fixture("echo-argv.mjs"), "--db", path, "list", "--json"]);

    expect(JSON.parse(stdout)).toEqual(["--db", path, "list", "--json"]);
  });
});

describe("failure handling", () => {
  it("reports a missing executable as executable-not-found", async () => {
    const error = await realRun([], {
      executable: "people-context-definitely-not-installed-xyzzy",
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(PeopleContextCliError);
    expect((error as PeopleContextCliError).kind).toBe("executable-not-found");
    expect((error as PeopleContextCliError).hint).toContain("plugin settings");
  });

  it("reports a non-zero exit with the bounded stderr text", async () => {
    const error = await realRun([fixture("exit-with-error.mjs")]).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(PeopleContextCliError);
    expect((error as PeopleContextCliError).kind).toBe("exit");
    expect((error as PeopleContextCliError).message).toContain("exit code 3");
    expect((error as PeopleContextCliError).message).toContain("database is locked");
  });

  it("stops a run that overstays its timeout", async () => {
    const started = Date.now();

    const error = await realRun([fixture("hang.mjs")], {
      timeoutMs: 250,
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(PeopleContextCliError);
    expect((error as PeopleContextCliError).kind).toBe("timeout");
    expect(Date.now() - started).toBeLessThan(10_000);
  });

  it("cancels a running read when its signal aborts", async () => {
    const controller = new AbortController();
    const pending = realRun([fixture("hang.mjs")], {
      signal: controller.signal,
    }).catch((caught: unknown) => caught);
    // The abort lands after the spawn, which is the case the panes actually hit when a
    // second person is selected while the first brief is still being read.
    setTimeout(() => {
      controller.abort();
    }, 50);

    const aborted = await pending;

    expect(aborted).toBeInstanceOf(PeopleContextCliError);
    expect((aborted as PeopleContextCliError).kind).toBe("aborted");
  }, 15_000);

  it("refuses before spawning when the signal is already aborted", async () => {
    const spawner = recordingSpawner();

    const error = await runCli({
      executable: "pctx",
      args: ["list", "--json"],
      env: {},
      timeoutMs: 1_000,
      maxOutputBytes: 1_000,
      signal: AbortSignal.abort(),
      spawn: spawner.spawn,
    }).catch((caught: unknown) => caught);

    expect((error as PeopleContextCliError).kind).toBe("aborted");
    expect(spawner.calls).toHaveLength(0);
  });

  it("stops and reports a run whose stdout exceeds the cap", async () => {
    const error = await realRun([fixture("flood.mjs"), "stdout"], {
      maxOutputBytes: 1024,
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(PeopleContextCliError);
    expect((error as PeopleContextCliError).kind).toBe("oversized-output");
    expect((error as PeopleContextCliError).message).toContain("stdout");
  });

  it("stops and reports a run whose stderr exceeds the cap", async () => {
    const error = await realRun([fixture("flood.mjs"), "stderr"], {
      maxOutputBytes: 1024,
    }).catch((caught: unknown) => caught);

    expect((error as PeopleContextCliError).kind).toBe("oversized-output");
    expect((error as PeopleContextCliError).message).toContain("stderr");
  });

  it("terminates the child when the output cap is hit", async () => {
    const spawner = recordingSpawner((child) => {
      child.stdout.emit("x".repeat(64));
    });

    const error = await runCli({
      executable: "pctx",
      args: [],
      env: {},
      timeoutMs: 5_000,
      maxOutputBytes: 8,
      spawn: spawner.spawn,
    }).catch((caught: unknown) => caught);

    expect((error as PeopleContextCliError).kind).toBe("oversized-output");
    expect(spawner.only().child.kills).toEqual(["SIGKILL"]);
  });

  it("settles once: a later close after a timeout does not change the outcome", async () => {
    const spawner = recordingSpawner();
    const pending = runCli({
      executable: "pctx",
      args: [],
      env: {},
      timeoutMs: 10,
      maxOutputBytes: 1_000,
      spawn: spawner.spawn,
    }).catch((caught: unknown) => caught);

    const error = await pending;
    spawner.only().child.emitClose(0);

    expect((error as PeopleContextCliError).kind).toBe("timeout");
  });
});

describe("scrubSecrets", () => {
  it("redacts an inherited database key from surfaced text", () => {
    const env = { PEOPLE_CONTEXT_DB_KEY: "s3cret-passphrase" };

    expect(scrubSecrets("failed opening with s3cret-passphrase", env)).toBe(
      "failed opening with [redacted]",
    );
  });

  it("leaves text alone when no key is present", () => {
    expect(scrubSecrets("plain message", {})).toBe("plain message");
    expect(scrubSecrets("plain message", { PEOPLE_CONTEXT_DB_KEY: "  " })).toBe("plain message");
  });
});
