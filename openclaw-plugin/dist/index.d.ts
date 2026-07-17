import type { DefinedToolPluginEntry } from "openclaw/plugin-sdk/tool-plugin";

export type Config = {
  baseUrl?: string;
  path?: string;
};

export type McpToolResult = {
  isError?: boolean;
  structuredContent?: unknown;
  content?: unknown;
};

export declare function serverUrl(config: Config): URL;

export declare function parseMcpToolResult(
  result: McpToolResult,
  toolName: string,
): unknown;

declare const plugin: DefinedToolPluginEntry;
export default plugin;
