import { describe, expect, it } from "vitest";

import { PeopleContextCliError } from "./bridge.js";
import { MISSING_KEY_MESSAGE, PeopleContextClient } from "./client.js";
import { DEFAULT_SETTINGS, type PeopleContextSettings } from "./settings.js";
import { recordingSpawner, succeedingSpawner } from "./testing/fake-process.js";

const INDEX_JSON = JSON.stringify({
  format: "people-context-person-index",
  version: 1,
  generated_at: "2026-08-11T08:09:14.776653Z",
  include_deleted: false,
  people: [
    {
      id: "01KZQXWK571FJAF03F6H63A85Z",
      canonical_name: "Bobby; rm -rf ~ $(whoami)",
      aliases: [],
      summary: null,
      is_self: false,
      deleted: false,
    },
  ],
});

const BRIEF_JSON = JSON.stringify({
  format: "people-context-brief",
  version: 1,
  generated_at: "2026-08-11T08:09:18.333078Z",
  disclosure: { include_sensitive: false, context: "ordinary", guidance: "ordinary", notice: "" },
  person: {
    id: "01KZQXWK571FJAF03F6H63A85Z",
    canonical_name: "Bobby; rm -rf ~ $(whoami)",
    aliases: [],
    summary: null,
    is_self: false,
  },
  guidance: {},
});

function client(
  spawn: ReturnType<typeof recordingSpawner>,
  overrides: Partial<PeopleContextSettings> = {},
  env: NodeJS.ProcessEnv = {},
): PeopleContextClient {
  return new PeopleContextClient({
    settings: () => ({ ...DEFAULT_SETTINGS, ...overrides }),
    env,
    spawn: spawn.spawn,
    timeoutMs: 1_000,
    maxOutputBytes: 1_000_000,
  });
}

describe("reading the index", () => {
  it("runs list --json with no other flags", async () => {
    const spawner = succeedingSpawner(INDEX_JSON);

    const document = await client(spawner).listPeople();

    expect(spawner.only().args).toEqual(["list", "--json"]);
    expect(document.people).toHaveLength(1);
  });

  it("prefixes the typed global flags in a fixed order", async () => {
    const spawner = succeedingSpawner(INDEX_JSON);

    await client(spawner, { databasePath: "/tmp/a b.db", encryptedDatabase: true }, {
      PEOPLE_CONTEXT_DB_KEY: "passphrase",
    }).listPeople();

    expect(spawner.only().args).toEqual([
      "--db",
      "/tmp/a b.db",
      "--encrypted",
      "list",
      "--json",
    ]);
  });
});

describe("reading a brief", () => {
  it("addresses the person by the stable id from the index", async () => {
    const indexSpawner = succeedingSpawner(INDEX_JSON);
    const briefSpawner = succeedingSpawner(BRIEF_JSON);

    const index = await client(indexSpawner).listPeople();
    const personId = index.people[0]?.id ?? "";
    await client(briefSpawner).getBrief(personId);

    const args = briefSpawner.only().args;
    expect(args).toEqual(["brief", "--json", "--", "01KZQXWK571FJAF03F6H63A85Z"]);
    // The display name reached the plugin as data and never became an argument.
    expect(args.join(" ")).not.toContain("Bobby");
    expect(args.join(" ")).not.toContain("$(whoami)");
  });

  it("passes an option-shaped id positionally rather than rejecting the person", async () => {
    const spawner = succeedingSpawner(BRIEF_JSON);

    await client(spawner).getBrief("--include-sensitive");

    // The separator is what keeps this inert: the CLI reads it as the person, not as a flag.
    expect(spawner.only().args).toEqual(["brief", "--json", "--", "--include-sensitive"]);
  });

  it("refuses a blank person id without spawning", async () => {
    const spawner = recordingSpawner();

    await expect(client(spawner).getBrief("   ")).rejects.toThrowError(/unusable person id/);
    expect(spawner.calls).toHaveLength(0);
  });

  it("never passes --include-sensitive", async () => {
    const spawner = succeedingSpawner(BRIEF_JSON);

    await client(spawner, { encryptedDatabase: true }, {
      PEOPLE_CONTEXT_DB_KEY: "passphrase",
    }).getBrief("01KZQXWK571FJAF03F6H63A85Z");

    expect(spawner.only().args).not.toContain("--include-sensitive");
  });
});

describe("encrypted databases", () => {
  it("inherits the key through the environment instead of an argument", async () => {
    const spawner = succeedingSpawner(INDEX_JSON);
    const env = { PATH: "/usr/bin", PEOPLE_CONTEXT_DB_KEY: "passphrase" };

    await client(spawner, { encryptedDatabase: true }, env).listPeople();

    const call = spawner.only();
    expect(call.options.env).toBe(env);
    expect(call.args.join(" ")).not.toContain("passphrase");
  });

  it("refuses with the canonical CLI message when the key was not inherited", async () => {
    const spawner = recordingSpawner();

    const error = await client(spawner, { encryptedDatabase: true }, { PATH: "/usr/bin" })
      .listPeople()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(PeopleContextCliError);
    expect((error as PeopleContextCliError).kind).toBe("missing-key");
    expect((error as PeopleContextCliError).message).toBe(MISSING_KEY_MESSAGE);
    expect((error as PeopleContextCliError).hint).toContain("Launch Obsidian");
  });

  it("treats a blank key as missing and never falls back to plaintext", async () => {
    const spawner = recordingSpawner();

    await expect(
      client(spawner, { encryptedDatabase: true }, { PEOPLE_CONTEXT_DB_KEY: "   " }).listPeople(),
    ).rejects.toThrowError(PeopleContextCliError);
    // No process ran at all, so no read could have happened against a plaintext database.
    expect(spawner.calls).toHaveLength(0);
  });

  it("does not require a key when encryption is off", async () => {
    const spawner = succeedingSpawner(INDEX_JSON);

    await client(spawner, { encryptedDatabase: false }, {}).listPeople();

    expect(spawner.only().args).not.toContain("--encrypted");
  });
});

describe("settings changes", () => {
  it("takes effect on the next read without rebuilding the client", async () => {
    const spawner = succeedingSpawner(INDEX_JSON);
    let settings: PeopleContextSettings = { ...DEFAULT_SETTINGS };
    const live = new PeopleContextClient({
      settings: () => settings,
      env: {},
      spawn: spawner.spawn,
    });

    await live.listPeople();
    settings = { ...settings, databasePath: "/tmp/other.db" };
    await live.listPeople();

    expect(spawner.calls[0]?.args).toEqual(["list", "--json"]);
    expect(spawner.calls[1]?.args).toEqual(["--db", "/tmp/other.db", "list", "--json"]);
  });
});
