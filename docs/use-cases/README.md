# Use-case gallery

Five narrative recipes for the workflows the [evaluation tasks](../evals.md#the-tasks) abstract into fixed
questions. Each one walks through a real situation end to end: what you ask, what the agent or CLI does, what
comes back, and what stays local.

| Recipe | Situation |
| --- | --- |
| [Your first week with a new team](first-week-with-a-new-team.md) | Going from an empty store to useful context |
| [Ten minutes before a meeting](before-a-meeting.md) | Recalling who someone is and how to talk to them |
| [Staying in touch on purpose](staying-in-touch.md) | Noticing who has gone quiet, and acting on it |
| [Moving to a new laptop](moving-to-a-new-laptop.md) | Carrying the store to a second device |
| [Auditing what the agent can see](auditing-what-the-agent-can-see.md) | Checking disclosure, quality, and footprint |

## How to read these

Commands are shown as `pctx …`; if you have not installed the CLI, prefix them with
`uvx --from people-context` as in the [README quick start](../../README.md#quick-start). Tool names in
`monospace` such as `resolve_person` are MCP tools your agent calls on your behalf — you ask in plain language
and the agent picks the tool. See [mcp-interface.md](../mcp-interface.md) for the full inventory.

Sample output is illustrative. It shows the shape of a result, not a promise about wording.

## Where your data goes

Every recipe runs against your local SQLite database. The store, the CLI, and the MCP server are local: no step
here uploads your database, and no step requires an account.

The agent you use is a separate trust boundary. When you drive these recipes through a cloud-hosted assistant,
whatever a tool call returns — names, affiliations, facts, interaction summaries — is sent to that model provider
like any other prompt content, and their retention terms apply to it. The server's job is to bound what leaves:
tool results are scoped to what you asked for, and sensitive and restricted records are withheld unless the
operator enabled the elevated tool in the server's own environment.

What the store does **not** keep is a record of what was read. The audit log covers create, update, merge, and
forget — mutations, not disclosures — so there is no local trail you can use afterwards to reconstruct which
names or facts a given tool call sent to a provider. Decide what to record on that basis, not on the assumption
that reads can be reviewed later. Commands you run yourself with `pctx` involve no model at all. See
[privacy-and-safety.md](../privacy-and-safety.md) for the disclosure model and threat notes.

## Try them without your own data

`pctx demo --reset` seeds a dedicated fictional database and prints commands to run against it, so you can walk
any recipe end to end before deciding what to record about real people. The demo store is separate from your
real one and ignores your configured database path entirely — see [cli.md](../cli.md#packaged-demo).
