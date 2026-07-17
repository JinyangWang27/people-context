import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

const configSchema = Type.Object({
  baseUrl: Type.Optional(
    Type.String({
      description: "Base URL of the people-context HTTP bridge or MCP HTTP server.",
      default: "http://127.0.0.1:8765",
    })
  ),
});

type Config = {
  baseUrl?: string;
};

async function post<T>(baseUrl: string, path: string, body: unknown): Promise<T> {
  const url = new URL(path, baseUrl.replace(/\/$/, ""));
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "unknown error");
    throw new Error(`people-context request failed: ${response.status} ${text}`);
  }

  return response.json() as Promise<T>;
}

export default defineToolPlugin({
  id: "people-context",
  name: "People Context",
  description:
    "Resolve and retrieve contextual knowledge about people the user mentions.",
  configSchema,
  tools: (tool) => [
    tool({
      name: "people_resolve",
      label: "Resolve Person",
      description:
        "Resolve a name, nickname, or partial reference to one or more known people.",
      parameters: Type.Object({
        query: Type.String({
          description: "Name, nickname, or partial reference to resolve.",
        }),
        limit: Type.Optional(
          Type.Integer({
            description: "Maximum number of candidates to return.",
            default: 5,
            minimum: 1,
            maximum: 20,
          })
        ),
        org: Type.Optional(
          Type.String({
            description: "Optional organization hint to disambiguate.",
          })
        ),
        role: Type.Optional(
          Type.String({
            description: "Optional role hint to disambiguate.",
          })
        ),
        relationship: Type.Optional(
          Type.String({
            description: "Optional relationship hint to disambiguate.",
          })
        ),
      }),
      async execute(
        { query, limit, org, role, relationship },
        config: Config
      ) {
        const baseUrl = config.baseUrl ?? "http://127.0.0.1:8765";
        return post(baseUrl, "/resolve", {
          query,
          limit: limit ?? 5,
          hints: { org, role, relationship },
        });
      },
    }),

    tool({
      name: "people_context",
      label: "Get Person Context",
      description:
        "Return a minimal-disclosure context bundle for a known person.",
      parameters: Type.Object({
        person_id: Type.String({
          description: "The person id returned by people_resolve.",
        }),
        purpose: Type.Optional(
          Type.String({
            description: "Why the context is needed, e.g. 'communication' or 'scheduling'.",
            default: "communication",
          })
        ),
        max_items: Type.Optional(
          Type.Integer({
            description: "Disclosure budget for facts and interactions.",
            default: 10,
            minimum: 0,
            maximum: 50,
          })
        ),
        include_sensitive: Type.Optional(
          Type.Boolean({
            description: "Whether to include sensitive-tagged records.",
            default: false,
          })
        ),
      }),
      async execute(
        { person_id, purpose, max_items, include_sensitive },
        config: Config
      ) {
        const baseUrl = config.baseUrl ?? "http://127.0.0.1:8765";
        return post(baseUrl, "/context", {
          person_id,
          purpose: purpose ?? "communication",
          max_items: max_items ?? 10,
          include_sensitive: include_sensitive ?? false,
        });
      },
    }),

    tool({
      name: "people_communication_guidance",
      label: "Get Communication Guidance",
      description:
        "Return traits, friction history, reminders, and the user's communication philosophy for a person.",
      parameters: Type.Object({
        person_id: Type.String({
          description: "The person id returned by people_resolve.",
        }),
        situation: Type.Optional(
          Type.String({
            description: "Brief description of the situation to tailor guidance.",
          })
        ),
      }),
      async execute({ person_id, situation }, config: Config) {
        const baseUrl = config.baseUrl ?? "http://127.0.0.1:8765";
        return post(baseUrl, "/guidance", {
          person_id,
          situation,
        });
      },
    }),

    tool({
      name: "people_remember",
      label: "Remember Person",
      description:
        "Create or update a person record, including aliases and summary.",
      parameters: Type.Object({
        name: Type.String({
          description: "Canonical name of the person.",
        }),
        summary: Type.Optional(
          Type.String({
            description: "Short summary of who the person is.",
          })
        ),
        aliases: Type.Optional(
          Type.Array(
            Type.Object({
              value: Type.String(),
              kind: Type.Optional(
                Type.String({
                  description: "nickname, native_script, transliteration, handle, former_name, or other.",
                })
              ),
              lang: Type.Optional(Type.String()),
              script: Type.Optional(Type.String()),
            })
          )
        ),
        is_self: Type.Optional(
          Type.Boolean({
            description: "Whether this person is the user themselves.",
            default: false,
          })
        ),
      }),
      async execute(
        { name, summary, aliases, is_self },
        config: Config
      ) {
        const baseUrl = config.baseUrl ?? "http://127.0.0.1:8765";
        return post(baseUrl, "/remember", {
          name,
          summary,
          aliases: aliases ?? [],
          is_self: is_self ?? false,
        });
      },
    }),
  ],
});
