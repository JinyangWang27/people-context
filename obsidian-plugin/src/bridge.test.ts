import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  type ChildProcessLike,
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_TIMEOUT_MS,
  PeopleContextCliError,
  runCli,
  scrubSecrets,
  terminateWith,
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

describe("output decoding", () => {
  it("decodes a multibyte character split across two stdout chunks", async () => {
    // Node hands over whatever the pipe delivered, which can cut a UTF-8 sequence in half.
    // Decoding each chunk on its own would turn both halves into replacement characters.
    const payload = Buffer.from('{"name":"Zoë Ødegård 陳大文 🙂"}', "utf8");
    const split = payload.indexOf(Buffer.from("ë", "utf8")) + 1;
    const spawner = recordingSpawner((child) => {
      child.stdout.emit(payload.subarray(0, split));
      child.stdout.emit(payload.subarray(split));
      child.emitClose(0);
    });

    const stdout = await runCli({
      executable: "pctx",
      args: [],
      env: {},
      timeoutMs: 1_000,
      maxOutputBytes: 1_000,
      spawn: spawner.spawn,
    });

    expect(stdout).toBe('{"name":"Zoë Ødegård 陳大文 🙂"}');
    expect(stdout).not.toContain("\uFFFD");
  });

  it("bounds output by bytes, not by decoded characters", async () => {
    // A cap measured in characters would let a multibyte payload exceed the byte budget.
    const spawner = recordingSpawner((child) => {
      child.stdout.emit(Buffer.from("陳".repeat(10), "utf8"));
    });

    const error = await runCli({
      executable: "pctx",
      args: [],
      env: {},
      timeoutMs: 1_000,
      maxOutputBytes: 20,
      spawn: spawner.spawn,
    }).catch((caught: unknown) => caught);

    expect((error as PeopleContextCliError).kind).toBe("oversized-output");
  });

  it("preserves non-ASCII output through a real process", async () => {
    const stdout = await realRun([fixture("echo-argv.mjs"), "Zoë Ødegård 陳大文 🙂"]);

    expect(JSON.parse(stdout)).toEqual(["Zoë Ødegård 陳大文 🙂"]);
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

describe("process-tree termination", () => {
  function child(pid: number | undefined): ChildProcessLike & { kills: unknown[] } {
    const kills: unknown[] = [];
    return {
      pid,
      stdout: null,
      stderr: null,
      on: () => undefined,
      kill: (signal?: NodeJS.Signals | number) => {
        kills.push(signal);
        return true;
      },
      kills,
    };
  }

  it("signals the whole process group on POSIX", () => {
    const killed: [number, string][] = [];
    const spawner = recordingSpawner();

    terminateWith({
      platform: "linux",
      kill: (pid, signal) => killed.push([pid, signal]),
      spawn: spawner.spawn,
    })(child(4242));

    // Negative pid is the group the detached child leads, not just the child.
    expect(killed).toEqual([[-4242, "SIGKILL"]]);
    expect(spawner.calls).toHaveLength(0);
  });

  it("tears down the tree with taskkill on Windows", () => {
    const spawner = recordingSpawner();
    const target = child(4242);

    terminateWith({
      platform: "win32",
      kill: () => {
        throw new Error("process groups do not exist on Windows");
      },
      spawn: spawner.spawn,
    })(target);

    // `/t` is what reaches the Python child a console-script launcher started; without it the
    // launcher dies and the process holding the database survives.
    const call = spawner.only();
    expect(call.command).toBe("taskkill");
    expect(call.args).toEqual(["/pid", "4242", "/t", "/f"]);
    expect(call.options.shell).toBe(false);
    expect(call.options.windowsHide).toBe(true);
    expect(target.kills).toEqual([]);
  });

  it("falls back to the direct child when spawning taskkill throws synchronously", () => {
    const target = child(4242);

    terminateWith({
      platform: "win32",
      kill: () => undefined,
      spawn: () => {
        throw new Error("taskkill not found");
      },
    })(target);

    expect(target.kills).toEqual(["SIGKILL"]);
  });

  it("falls back when taskkill fails to launch asynchronously", () => {
    // This is the real production shape: `spawn` returns a child and reports ENOENT — or an
    // endpoint policy blocking taskkill — as an `error` event afterwards. A synchronous
    // try/catch never sees it, and an unlistened `error` would crash the host.
    const spawner = recordingSpawner();
    const target = child(4242);

    terminateWith({ platform: "win32", kill: () => undefined, spawn: spawner.spawn })(target);
    expect(target.kills).toEqual([]);

    spawner.only().child.emitError(Object.assign(new Error("spawn taskkill ENOENT"), {
      code: "ENOENT",
    }));

    expect(target.kills).toEqual(["SIGKILL"]);
  });

  it("falls back when taskkill exits unsuccessfully", () => {
    const spawner = recordingSpawner();
    const target = child(4242);

    terminateWith({ platform: "win32", kill: () => undefined, spawn: spawner.spawn })(target);
    // 1 is what taskkill returns for access denied; the tree is still standing.
    spawner.only().child.emitClose(1);

    expect(target.kills).toEqual(["SIGKILL"]);
  });

  it("does not touch the child when taskkill succeeds", () => {
    const spawner = recordingSpawner();
    const target = child(4242);

    terminateWith({ platform: "win32", kill: () => undefined, spawn: spawner.spawn })(target);
    spawner.only().child.emitClose(0);

    expect(target.kills).toEqual([]);
  });

  it("falls back exactly once when taskkill both errors and exits", () => {
    const spawner = recordingSpawner();
    const target = child(4242);

    terminateWith({ platform: "win32", kill: () => undefined, spawn: spawner.spawn })(target);
    spawner.only().child.emitError(new Error("boom"));
    spawner.only().child.emitClose(1);

    expect(target.kills).toEqual(["SIGKILL"]);
  });

  it("falls back to the direct child when the group is already gone", () => {
    const target = child(4242);

    terminateWith({
      platform: "darwin",
      kill: () => {
        throw new Error("ESRCH");
      },
      spawn: recordingSpawner().spawn,
    })(target);

    expect(target.kills).toEqual(["SIGKILL"]);
  });

  it("kills the child directly when there is no pid to address a tree with", () => {
    const spawner = recordingSpawner();
    const target = child(undefined);

    terminateWith({ platform: "linux", kill: () => undefined, spawn: spawner.spawn })(target);

    expect(target.kills).toEqual(["SIGKILL"]);
    expect(spawner.calls).toHaveLength(0);
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
