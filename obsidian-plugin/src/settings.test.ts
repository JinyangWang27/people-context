import { describe, expect, it } from "vitest";

import {
  DEFAULT_SETTINGS,
  type PeopleContextSettings,
  briefArguments,
  globalArguments,
  isUsablePersonId,
  listArguments,
  normalizeSettings,
} from "./settings.js";

function settings(overrides: Partial<PeopleContextSettings> = {}): PeopleContextSettings {
  return { ...DEFAULT_SETTINGS, ...overrides };
}

describe("normalizeSettings", () => {
  it("falls back to defaults for absent or wrongly typed persisted data", () => {
    expect(normalizeSettings(undefined)).toEqual(DEFAULT_SETTINGS);
    expect(normalizeSettings({ executablePath: 7, encryptedDatabase: "yes" })).toEqual(
      DEFAULT_SETTINGS,
    );
  });

  it("defaults refresh to on-open and rejects unknown policies", () => {
    expect(DEFAULT_SETTINGS.refreshPolicy).toBe("on-open");
    expect(normalizeSettings({ refreshPolicy: "hourly" }).refreshPolicy).toBe("on-open");
    expect(normalizeSettings({ refreshPolicy: "manual" }).refreshPolicy).toBe("manual");
  });

  it("treats a blank executable as the default and trims paths", () => {
    const normalized = normalizeSettings({ executablePath: "   ", databasePath: "  /tmp/a.db  " });

    expect(normalized.executablePath).toBe("pctx");
    expect(normalized.databasePath).toBe("/tmp/a.db");
  });

  it("only enables encryption for a literal true", () => {
    expect(normalizeSettings({ encryptedDatabase: "true" }).encryptedDatabase).toBe(false);
    expect(normalizeSettings({ encryptedDatabase: true }).encryptedDatabase).toBe(true);
  });
});

describe("argument arrays", () => {
  it("omits both global flags by default", () => {
    expect(globalArguments(settings())).toEqual([]);
    expect(listArguments(settings())).toEqual(["list", "--json"]);
  });

  it("passes the database path as one separate argument", () => {
    const path = "/home/a b/People's \"data\"; rm -rf $(pwd) `x` & | %USERPROFILE% ^db.sqlite";

    expect(globalArguments(settings({ databasePath: path }))).toEqual(["--db", path]);
  });

  it("adds --encrypted after the database path when encryption is on", () => {
    const args = listArguments(settings({ databasePath: "/tmp/x.db", encryptedDatabase: true }));

    expect(args).toEqual(["--db", "/tmp/x.db", "--encrypted", "list", "--json"]);
  });

  it("never asks for soft-deleted people or sensitive disclosure", () => {
    const args = [
      ...listArguments(settings({ encryptedDatabase: true, databasePath: "/tmp/x.db" })),
      ...briefArguments(settings(), "01KZQXWK571FJAF03F6H63A85Z"),
    ];

    expect(args).not.toContain("--all");
    expect(args).not.toContain("--include-sensitive");
  });

  it("addresses a brief by stable id, after an option separator", () => {
    expect(briefArguments(settings(), "01KZQXWK571FJAF03F6H63A85Z")).toEqual([
      "brief",
      "--json",
      "--",
      "01KZQXWK571FJAF03F6H63A85Z",
    ]);
  });

  it("puts every option before the separator so the id is always positional", () => {
    const args = briefArguments(
      settings({ databasePath: "/tmp/x.db", encryptedDatabase: true }),
      "-rf",
    );

    expect(args).toEqual(["--db", "/tmp/x.db", "--encrypted", "brief", "--json", "--", "-rf"]);
    expect(args.indexOf("--")).toBe(args.length - 2);
  });
});

describe("person id handling", () => {
  // The identifier contract admits any non-blank string, and a database restored from a sync
  // bundle can carry one. Narrowing that grammar here would reject a whole valid index, so
  // these are all accepted as data and made safe by the `--` separator instead.
  const opaque = [
    "01KZQXWK571FJAF03F6H63A85Z",
    "person:alice",
    "urn:uuid:8f14e45f-ea0f-4a1b-9f2c-1d4f0a2b3c4d",
    "Bobby; rm -rf ~",
    "$(whoami)",
    "`id`",
    "a & b",
    "a | b",
    "a > out",
    "%USERPROFILE%",
    "^db",
    '"quoted"',
    "'quoted'",
    "with space",
    "--include-sensitive",
    "-rf",
    "../../etc/passwd",
    "x".repeat(200),
    "陳大文",
  ];

  it.each(opaque)("accepts %j as an opaque id and keeps it positional", (value) => {
    expect(isUsablePersonId(value)).toBe(true);

    const args = briefArguments(settings(), value);

    expect(args).toEqual(["brief", "--json", "--", value]);
    // Whatever the id looks like, it is the last element and follows the separator, so the
    // CLI parser reads it as the person and never as an option.
    expect(args.at(-1)).toBe(value);
    expect(args.at(-2)).toBe("--");
  });

  const unusable = ["", "   ", "\u0000", "a\u0000b"];

  it.each(unusable)("rejects %j, which cannot be an argument at all", (value) => {
    expect(isUsablePersonId(value)).toBe(false);
    expect(() => briefArguments(settings(), value)).toThrowError(/unusable person id/);
  });

  it("rejects a non-string id", () => {
    expect(isUsablePersonId(undefined)).toBe(false);
    expect(isUsablePersonId(42)).toBe(false);
  });

  it("does not echo the rejected value back into the message", () => {
    expect(() => briefArguments(settings(), "  ")).toThrowError(
      "Refusing to run a command for an unusable person id.",
    );
  });
});
