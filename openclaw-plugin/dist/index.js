import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { Type } from "typebox";
const configSchema = Type.Object({
    baseUrl: Type.Optional(Type.String({
        description: "Base URL of the people-context Streamable HTTP MCP server.",
        default: "http://127.0.0.1:8765",
    })),
    path: Type.Optional(Type.String({
        description: "HTTP path for the MCP endpoint.",
        default: "/mcp",
    })),
}, { additionalProperties: false });
function stripTrailingSlashes(value) {
    let endIndex = value.length;
    while (endIndex > 0 && value[endIndex - 1] === "/") {
        endIndex -= 1;
    }
    return value.slice(0, endIndex);
}
export function serverUrl(config) {
    const base = stripTrailingSlashes(config.baseUrl ?? "http://127.0.0.1:8765");
    const path = (config.path ?? "/mcp").replace(/^\/+/, "");
    return new URL(`${base}/${path}`);
}
function extractTextContent(result) {
    if (!Array.isArray(result.content)) {
        return "";
    }
    return result.content
        .filter((block) => typeof block === "object" &&
        block !== null &&
        block.type === "text" &&
        typeof block.text === "string")
        .map((block) => block.text)
        .join("\n")
        .trim();
}
function stringifyDetails(value) {
    if (value === undefined || value === null) {
        return "";
    }
    try {
        const serialized = JSON.stringify(value);
        return serialized ?? String(value);
    }
    catch {
        return String(value);
    }
}
export function parseMcpToolResult(result, toolName) {
    const text = extractTextContent(result);
    if (result.isError === true) {
        const details = text || stringifyDetails(result.structuredContent);
        throw new Error(details
            ? `MCP tool "${toolName}" failed: ${details}`
            : `MCP tool "${toolName}" failed`);
    }
    if (result.structuredContent !== undefined && result.structuredContent !== null) {
        return result.structuredContent;
    }
    if (!text) {
        throw new Error(`MCP tool "${toolName}" returned no usable content`);
    }
    try {
        return JSON.parse(text);
    }
    catch {
        return text;
    }
}
async function callMcpTool(config, name, args, signal) {
    signal?.throwIfAborted();
    const client = new Client({
        name: "openclaw-people-context",
        version: "0.2.0",
    });
    const transport = new StreamableHTTPClientTransport(serverUrl(config));
    let connected = false;
    try {
        await client.connect(transport);
        connected = true;
        signal?.throwIfAborted();
        const result = await client.callTool({ name, arguments: args });
        return parseMcpToolResult(result, name);
    }
    finally {
        if (connected) {
            await client.close();
        }
    }
}
export default defineToolPlugin({
    id: "people-context",
    name: "People Context",
    description: "Resolve and retrieve contextual knowledge about people the user mentions.",
    configSchema,
    tools: (tool) => [
        tool({
            name: "people_resolve",
            label: "Resolve Person",
            description: "Resolve a name, nickname, or partial reference to one or more known people.",
            parameters: Type.Object({
                query: Type.String({
                    description: "Name, nickname, or partial reference to resolve.",
                }),
                limit: Type.Optional(Type.Integer({
                    description: "Maximum number of candidates to return.",
                    default: 5,
                    minimum: 1,
                    maximum: 20,
                })),
                org: Type.Optional(Type.String({
                    description: "Optional organization hint to disambiguate.",
                })),
                role: Type.Optional(Type.String({
                    description: "Optional role hint to disambiguate.",
                })),
                relationship: Type.Optional(Type.String({
                    description: "Optional relationship hint to disambiguate.",
                })),
            }),
            async execute({ query, limit, org, role, relationship }, config, context) {
                const hints = {};
                if (org !== undefined)
                    hints.org = org;
                if (role !== undefined)
                    hints.role = role;
                if (relationship !== undefined)
                    hints.relationship = relationship;
                return callMcpTool(config, "resolve_person", {
                    query,
                    limit: limit ?? 5,
                    hints,
                }, context.signal);
            },
        }),
        tool({
            name: "people_context",
            label: "Get Person Context",
            description: "Return a minimal-disclosure context bundle for a known person.",
            parameters: Type.Object({
                person_id: Type.String({
                    description: "The person id returned by people_resolve.",
                }),
                purpose: Type.Optional(Type.String({
                    description: "Why the context is needed, e.g. 'communication' or 'scheduling'.",
                    default: "communication",
                })),
                max_items: Type.Optional(Type.Integer({
                    description: "Disclosure budget for facts and interactions.",
                    default: 10,
                    minimum: 0,
                    maximum: 50,
                })),
            }),
            async execute({ person_id, purpose, max_items }, config, context) {
                return callMcpTool(config, "get_person_context", {
                    person_id,
                    purpose: purpose ?? "communication",
                    max_items: max_items ?? 10,
                }, context.signal);
            },
        }),
        tool({
            name: "people_communication_guidance",
            label: "Get Communication Guidance",
            description: "Return traits, friction history, reminders, and the user's communication philosophy for a person.",
            parameters: Type.Object({
                person_id: Type.String({
                    description: "The person id returned by people_resolve.",
                }),
                situation: Type.Optional(Type.String({
                    description: "Brief description of the situation to tailor guidance.",
                })),
            }),
            async execute({ person_id, situation }, config, context) {
                return callMcpTool(config, "get_communication_guidance", {
                    person_id,
                    situation,
                }, context.signal);
            },
        }),
        tool({
            name: "people_remember",
            label: "Remember Person",
            description: "Create or update a person record, including aliases and summary.",
            optional: true,
            parameters: Type.Object({
                name: Type.String({
                    description: "Canonical name of the person.",
                }),
                summary: Type.Optional(Type.String({
                    description: "Short summary of who the person is.",
                })),
                aliases: Type.Optional(Type.Array(Type.Object({
                    value: Type.String(),
                    kind: Type.Optional(Type.String({
                        description: "nickname, native_script, transliteration, handle, former_name, or other.",
                    })),
                    lang: Type.Optional(Type.String()),
                    script: Type.Optional(Type.String()),
                }))),
                is_self: Type.Optional(Type.Boolean({
                    description: "Whether this person is the user themselves.",
                    default: false,
                })),
            }),
            async execute({ name, summary, aliases, is_self }, config, context) {
                return callMcpTool(config, "remember_person", {
                    name,
                    summary,
                    aliases: aliases ?? [],
                    is_self: is_self ?? false,
                }, context.signal);
            },
        }),
    ],
});
