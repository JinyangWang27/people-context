# Claude Code plugin

The repository is also a self-hosted Claude Code plugin marketplace. The plugin launches `people-context-mcp` locally over MCP stdio; no hosted application server, public endpoint, domain, OAuth service, or shared database is required.

## Requirements

- Claude Code 2.1.196 or newer
- [`uv`](https://docs.astral.sh/uv/) available on `PATH`

## Install from GitHub

Add the marketplace:

```bash
claude plugin marketplace add JinyangWang27/people-context
```

Install the plugin:

```bash
claude plugin install people-context@people-context-plugins
```

Restart Claude Code or run `/reload-plugins`. Use `/mcp` to confirm that the `people-context` server is connected.

Inside an interactive Claude Code session, the equivalent commands are:

```text
/plugin marketplace add JinyangWang27/people-context
/plugin install people-context@people-context-plugins
/reload-plugins
```

## Local runtime

When the plugin is enabled, Claude Code starts this command automatically:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" people-context-mcp
```

`CLAUDE_PLUGIN_ROOT` points to Claude Code's installed copy of this repository.

The server resolves its database path with the standard chain documented in [cli.md](cli.md) and
[data-model.md](data-model.md): the `--db` flag, then `PEOPLE_CONTEXT_DB`, then a config file, then an
agent-workspace directory, and finally the XDG data default `~/.local/share/people-context/people.db`.
The plugin deliberately passes no `--db` flag, so the database lands at that XDG default. This location
lives in the user's home directory — entirely outside the plugin's installed copy — so it survives
marketplace updates, reinstalls, and uninstalls, and it is the same file the `people-context` CLI reads
by default, so `people-context show` and `people-context export` work against the plugin's data with no
extra configuration. To isolate or relocate the store, set `PEOPLE_CONTEXT_DB` in the environment that
launches Claude Code.

The MCP server uses stdio. It does not listen on a TCP port and is available only to the local Claude Code process that launched it.

## Bundled usage skill

The plugin ships one root-level skill at `skills/people-context-usage/SKILL.md`. Claude
Code discovers skills at the plugin root, not inside `.claude-plugin/`. The skill adds no
new tool or capability; it teaches agents to compose the existing tools correctly:

- resolve identity with `resolve_person` first and preserve the `ambiguous` candidate-list
  contract instead of guessing;
- read `get_person_context` for what is known and `get_communication_guidance` for how to
  communicate;
- capture extracted knowledge only through the strict `person`/`interaction`/`affiliation`/
  `fact` staged-candidate vocabulary, never as raw conversation text;
- treat `stage_candidates` as a proposal, `review_import` as inspection, and `commit_import`
  as an explicit, user-approved later write — never an automatic commit;
- treat the absence of `get_sensitive_person_context` and `export_data` from ordinary
  discovery as an intended privacy gate, not something to work around.

The skill is behavioural guidance only. It never enables elevated tools, never commits a
staged batch automatically, and never copies raw transcript text into candidates.

## User-invocable workflows

The plugin also ships three user-invoked workflow skills under `skills/`, each
invocable as a slash command in the `people-context` namespace. They are thin
compositions of the existing tools — they add no new tool, port, response field, or
write path — and model invocation is disabled so they run only when the user asks:

- `/people-context:who <query>` calls `resolve_person` and branches on its
  `ambiguous` flag (not candidate count): when `ambiguous` is false it reads the
  ranked top candidate with `get_person_context`; when `ambiguous` is true, or no
  candidate is returned, it surfaces the candidate list without guessing and performs
  no second read.
- `/people-context:remember <description>` records durable knowledge down one of
  these existing paths, resolving every referenced person first (so an existing
  contact is updated, not duplicated): an explicit person assertion uses
  `remember_person` with the resolved canonical name, while facts, affiliations, and
  interactions are staged with `stage_candidates` under a fixed non-content `source`
  label and left pending `review_import`. It acts on prior conversation only when the
  invocation explicitly asks for it, reports requests that fit neither path — notably
  relationships, which have no staging candidate type — as unsupported, never calls
  `commit_import`, and never copies raw text into candidate fields or the `source`.
- `/people-context:reminders [person]` lists active reminders; when a person is named
  it resolves the identity first and filters by the resolved id, surfacing ambiguity
  rather than silently dropping the filter.

None of the workflows call or suggest enabling the gated
`get_sensitive_person_context` or `export_data` tools.

## Security model

Installing this plugin executes the repository's Python code locally through `uv` with the permissions of your operating-system user. It is not a sandboxed, data-only extension. Install only revisions you trust.

The durable store is an unencrypted SQLite file at the resolved database path (by default
`~/.local/share/people-context/people.db`). Normal filesystem permissions and full-disk encryption are the at-rest security boundary. Anyone who can read that file can inspect its contents directly.

The default plugin configuration deliberately does not set either high-disclosure process capability:

- `get_person_context` returns only public and personal records; sensitive and restricted records are unavailable.
- `get_sensitive_person_context` is not registered unless the operator starts a different server process with `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1`.
- `export_data` is not registered unless the operator starts a different server process with `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1`. Use the human-operated `people-context export` CLI for routine portability.

MCP annotations are advisory client metadata, not authorization. The process-level capability gates above are what prevent prompt content from widening the disclosure boundary. Email and mbox Subject values are treated as attacker-controlled input and replaced with the neutral summary `Email correspondence` before staging or persistence.

See [Privacy and Safety](privacy-and-safety.md) for the complete threat model.

## Update and release

Refresh the marketplace and plugin after a new release is merged:

```bash
claude plugin marketplace update people-context-plugins
claude plugin update people-context@people-context-plugins
```

The plugin and marketplace entry use semantic versioning, starting at `0.1.0`. Bump both version fields whenever a release should become available to installed users. Ordinary commits that do not change the version are not treated as plugin updates.

## Local validation

From the repository root:

```bash
claude plugin validate . --strict
uv run people-context-mcp --help
uv run pytest -q tests/adapters/test_mcp_server.py tests/adapters/test_email_import.py
```

For an end-to-end local installation test:

```bash
claude plugin marketplace add ./ --scope local
claude plugin install people-context@people-context-plugins --scope local
```

Then restart Claude Code or run `/reload-plugins`, inspect `/mcp`, and exercise `resolve_person`, `get_person_context`, and `remember_person` against a temporary database. Confirm that `get_sensitive_person_context` and `export_data` are absent from ordinary tool discovery.

## Public Anthropic distribution

The self-hosted GitHub marketplace works without Anthropic review. Once it has been tested publicly, the same repository can be submitted for the Anthropic community plugin directory through the Claude Console plugin submission form.

Anthropic's `claude-plugins-official` marketplace is curated separately and currently has no public application process. A community-directory submission does not automatically add the plugin to the official marketplace.

Before submitting:

1. Run `claude plugin validate . --strict` on the intended release commit.
2. Confirm installation from a clean machine using the GitHub marketplace commands above.
3. Verify that all durable data remains at the resolved database path (by default the XDG data directory).
4. Confirm that the default tool surface excludes high-disclosure reads.
5. Document the local execution model, required `uv` dependency, tool behavior, and privacy properties.
