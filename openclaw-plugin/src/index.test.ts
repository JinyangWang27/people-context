import { describe, expect, it } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import plugin from "./index.js";

describe("people-context plugin", () => {
  it("exports the expected tools", () => {
    const metadata = getToolPluginMetadata(plugin);
    expect(metadata).toBeDefined();
    const tools = metadata!.tools.map((t) => t.name);
    expect(tools).toContain("people_resolve");
    expect(tools).toContain("people_context");
    expect(tools).toContain("people_communication_guidance");
    expect(tools).toContain("people_remember");
  });
});
