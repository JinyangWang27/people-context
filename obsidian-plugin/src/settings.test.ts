import { describe, expect, it } from "vitest";

import {
  DEFAULT_SETTINGS,
  type PeopleContextSettings,
  briefArguments,
  globalArguments,
  isSafePersonId,
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

  it("addresses a brief by stable id", () => {
    expect(briefArguments(settings(), "01KZQXWK571FJAF03F6H63A85Z")).toEqual([
      "brief",
      "01KZQXWK571FJAF03F6H63A85Z",
      "--json",
    ]);
  });
});

describe("person id validation", () => {
  const hostile = [
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
    "",
    "x".repeat(65),
  ];

  it.each(hostile)("rejects %j as a person id", (value) => {
    expect(isSafePersonId(value)).toBe(false);
    expect(() => briefArguments(settings(), value)).toThrowError(/unrecognized person id/);
  });

  it("does not echo the rejected value back into the message", () => {
    expect(() => briefArguments(settings(), "$(whoami)")).toThrowError(
      "Refusing to run a command for an unrecognized person id.",
    );
  });

  it("accepts the ids the CLI actually emits", () => {
    expect(isSafePersonId("01KZQXWK571FJAF03F6H63A85Z")).toBe(true);
  });
});
