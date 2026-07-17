import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import { describe, expect, it } from "vitest";
import plugin, { parseMcpToolResult, serverUrl } from "./index.js";

describe("people-context plugin", () => {
  it("exports the expected tools and keeps writes optional", () => {
    const metadata = getToolPluginMetadata(plugin);

    expect(metadata?.tools.map((tool) => tool.name)).toEqual([
      "people_resolve",
      "people_context",
      "people_communication_guidance",
      "people_remember",
    ]);
    expect(metadata?.tools.find((tool) => tool.name === "people_remember")).toMatchObject({
      optional: true,
    });
  });

  it("builds the default and configured MCP URLs", () => {
    expect(serverUrl({}).toString()).toBe("http://127.0.0.1:8765/mcp");
    expect(
      serverUrl({ baseUrl: "http://localhost:9000/", path: "/people/mcp" }).toString(),
    ).toBe("http://localhost:9000/people/mcp");
  });

  it("prefers structured content and parses JSON text", () => {
    expect(
      parseMcpToolResult({ structuredContent: { candidates: [] } }, "resolve_person"),
    ).toEqual({ candidates: [] });
    expect(
      parseMcpToolResult(
        { content: [{ type: "text", text: '{"created":true}' }] },
        "remember_person",
      ),
    ).toEqual({ created: true });
  });

  it("preserves plain text and surfaces MCP tool errors", () => {
    expect(
      parseMcpToolResult(
        { content: [{ type: "text", text: "plain response" }] },
        "get_person_context",
      ),
    ).toBe("plain response");

    expect(() =>
      parseMcpToolResult(
        {
          isError: true,
          content: [{ type: "text", text: "person not found" }],
        },
        "get_person_context",
      ),
    ).toThrow('MCP tool "get_person_context" failed: person not found');
  });
});
