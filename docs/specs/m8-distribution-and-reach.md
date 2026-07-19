# M8 — Distribution & reach

Status: Planned. See [docs/roadmap.md](../roadmap.md#m8--distribution--reach).

## Motivation

The project already has a working PyPI release pipeline (`.github/workflows/release.yml`, documented in
[docs/releasing.md](../releasing.md)) and a working self-hosted Claude Code plugin marketplace
([docs/claude-code-plugin.md](../claude-code-plugin.md)). It also already publishes a second, unrelated
artifact — the `openclaw-plugin/` package — through its own CI workflow
(`.github/workflows/package-publish.yml`) to the OpenClaw registry. What is missing is not a release mechanism;
it is *surface area*: someone has to already know this repository exists and be willing to `git clone` and
`uv sync` before trying it. The base install has three direct runtime dependencies (`mcp`, `pydantic`,
`python-ulid`, per `pyproject.toml`'s `[project]` `dependencies`), and the entire semantic-search stack
(`model2vec`, `sqlite-vec`) is an opt-in extra that ordinary startup and search never touch (see
[docs/privacy-and-safety.md](../privacy-and-safety.md#local-user-owned-no-surprise-network-activity)). That
small, optional-heavy footprint is exactly what makes the server cheap to bundle, containerize, and list in
registries without taking on a maintenance burden disproportionate to the code it ships.

This milestone turns "clone and build" into "one line in a client config," across every surface a prospective
user is likely to encounter first: the official MCP registry, the community directories people actually search
(Smithery, PulseMCP, mcp.so, Glama), the Claude Desktop extension flow, and the common editor/IDE MCP clients.

## Scope

In scope:

- verifying and documenting `uvx --from people-context people-context-mcp` as a zero-clone install path;
- an MCP registry `server.json` (or equivalent per-registry metadata files) at the repository root;
- a Claude Desktop extension (`.mcpb` bundle);
- one-line stdio configuration snippets for Claude Desktop, Cursor, Windsurf, and VS Code in the README;
- an optional Docker image and its CI publish job;
- README/docs updates reflecting all of the above.

Non-goals:

- any change to `domain`, `app`, `ports`, or the MCP tool surface — this milestone is packaging and docs only;
- authenticated or remote transport (still explicitly out of scope per
  [docs/mcp-interface.md](../mcp-interface.md));
- a hosted/managed version of the server; every distribution path here still runs the server locally under the
  user's own OS account, per the existing security model in
  [docs/claude-code-plugin.md](../claude-code-plugin.md#security-model);
- vendoring a Python interpreter or dependencies inside the Docker image beyond what `uv` already resolves.

## Design

### Zero-clone PyPI install

`pyproject.toml` already declares `[project.scripts] people-context-mcp = "people_context.adapters.mcp.server:main"`
and `people-context = "people_context.cli:main"`, and the package is built with `hatchling`
(`[build-system]`) and published via `.github/workflows/release.yml` using PyPI Trusted Publishing (see
[docs/releasing.md](../releasing.md)). No code change is required for `uvx --from people-context people-context-mcp` to work; this
deliverable is verification (a clean-machine run of `uvx people-context-mcp --help` and a stdio round trip) plus
a README quick-start edit that puts the `uvx` form ahead of the `git clone` + `uv sync` form.

### MCP registry and community directories

Add `server.json` at the repository root following the official MCP Registry server schema: a PyPI-distributed
server is represented by a `packages` entry with `"registryType": "pypi"`, the package identifier
`people-context` and its version, and `"transport": {"type": "stdio"}` — not by a raw `command`/`args`
invocation, which is not how the Registry models package-based distribution. The Registry verifies PyPI
ownership through an `mcp-name:` marker in the packaged README, so adding that marker (and keeping it present
through every release, since it ships inside the sdist/wheel README) is a deliverable of this milestone.
Registry initialization, validation, and publication use the Registry's own `mcp-publisher` tooling
(`mcp-publisher validate` in CI, `mcp-publisher publish` in the release flow) — not Claude's plugin validator,
which covers only the Claude Code plugin files. Once the `.mcpb` bundle below exists, it can be represented as
an additional `"registryType": "mcpb"` package entry carrying the artifact URL and its SHA-256.

Community directories (Smithery, PulseMCP, mcp.so, Glama) each have their own submission format; where a
directory requires an in-repo metadata file (for example a `smithery.yaml`), add it alongside `server.json`
rather than duplicating tool descriptions that already live in `docs/mcp-interface.md`. None of these files
execute anything themselves — they are static metadata pointing at the same PyPI/stdio distribution the plugin
and README already document.

### Claude Desktop extension (`.mcpb`)

An `.mcpb` bundle needs a manifest describing the server's stdio invocation, analogous in spirit to
`.claude-plugin/mcp.json`'s `{"mcpServers": {"people-context": {"type": "stdio", "command": "uv", "args": [...]}}}`
shape, but targeting the `uvx --from people-context people-context-mcp` invocation so the bundle does not need to vendor a project
directory. Packaging happens through a new `scripts/build-mcpb.*` step invoked from CI (there is currently no
`scripts/` directory in the repository; this would be the first script added), producing a downloadable
artifact attached to GitHub Releases alongside the existing PyPI publish step.

### Editor/IDE one-line configs

The README's existing "MCP client configuration" section already shows the Claude Code `claude mcp add` form and
a generic stdio JSON block (`README.md` lines ~87–106). Add equivalent blocks for Cursor (`.cursor/mcp.json`),
Windsurf, and VS Code (`.vscode/mcp.json`), all using the same `uvx --from people-context people-context-mcp` command so there is
exactly one canonical invocation to keep correct across every client, rather than one snippet per client with
independent `uv run --directory ...` paths.

### Optional Docker image

A new `Dockerfile` builds the package with `uv` in a multi-stage image, runs as a non-root user, and starts
`people-context-mcp` over stdio by default (Docker's own stdio-attach semantics make this usable from a client
that shells out to `docker run -i`). The database path resolves the same way it does everywhere else
(`config.py:resolve_db_path`), so the container's documented usage mounts a host directory at the resolved XDG
data path and sets `PEOPLE_CONTEXT_DB` explicitly rather than inventing container-specific configuration. A new
CI job publishes the image to GHCR on tagged releases, following the existing pattern of
`.github/workflows/release.yml` (PyPI) and `.github/workflows/package-publish.yml` (OpenClaw) — a third,
narrowly-scoped publish workflow rather than folding container publishing into either existing one.

## Migration needs

None. No schema, port, or domain change.

## CLI / MCP surface changes

None. Every deliverable in this milestone wraps the existing `people-context-mcp` stdio entrypoint
(`adapters/mcp/server.py:main`) and the existing `people-context` CLI entrypoint (`cli.py:main`); no new
command, flag, or MCP tool is added.

## Security / privacy considerations

- Registry and directory metadata files contain only public project information (name, description, repository
  URL, tool list already published in `docs/mcp-interface.md`) — no user data, no telemetry, no analytics
  hooks are introduced by any of these submissions.
- The Docker image changes *how* the same local process starts, not *what* it does: it still speaks stdio only
  by default, still resolves the database path through the existing precedence chain, and still makes no
  outbound network call except the existing, explicit `people-context reindex --semantic` model download path
  (see [docs/privacy-and-safety.md](../privacy-and-safety.md#local-user-owned-no-surprise-network-activity)).
  The image must not bake in `--http` as a default command, since loopback HTTP inside a container changes the
  effective trust boundary described in that document (container network namespaces are not the same as host
  loopback).
- The `.mcpb` bundle and every editor config continue to execute local Python with the launching user's
  filesystem permissions, per the existing "installed integrations execute local code" threat-model note in
  [docs/privacy-and-safety.md](../privacy-and-safety.md#threat-model-notes) — this milestone documents that
  fact prominently for each new distribution channel rather than implying any of them is a sandboxed extension.
- Trusted Publishing (already in place for PyPI) should be the model for any new publish credential this
  milestone adds (GHCR, community-directory API tokens): short-lived, workflow-scoped, least-privilege, never a
  long-lived secret committed anywhere.

## Testing strategy

- CI: an `mcp-publisher validate` step for `server.json`, run alongside — not folded into — the existing
  `claude plugin validate . --strict` step ([docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation)),
  which covers only the Claude Code plugin files and knows nothing about Registry metadata.
- CI: a Docker smoke job that builds the image, runs it with a temporary volume, and execs
  `people-context-mcp --help` and one real stdio round trip (resolve/remember/context), mirroring the existing
  `uv run people-context-mcp --help` and `tests/adapters/test_mcp_server.py` /
  `tests/adapters/test_email_import.py` validation already listed in
  [docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation).
- Manual: install from each new config snippet (Cursor, Windsurf, VS Code) on a clean machine and exercise
  `resolve_person` / `get_person_context` / `remember_person`, the same acceptance check already used for the
  Claude Code plugin's own "Local validation" section.
- Manual: install the `.mcpb` bundle in Claude Desktop and confirm the server connects without a local clone.
- No new `domain`/`app`/`ports`/adapter Python code is introduced, so no new fake-port or real-SQLite unit
  tests are required for this milestone.

## Open questions

1. Which Registry namespace should the server publish under — the GitHub-authenticated `io.github.jinyangwang27.*`
   form or a custom domain — and does that choice constrain a later rename?
2. Can the `.mcpb` bundle shell out to `uvx` at first run (simpler bundle, but a first-run network dependency
   to fetch the package), or does the Desktop extension format require vendoring a self-contained interpreter?
3. Is a single Docker image (stdio-only) sufficient, or should the milestone also ship an `--http` variant image
   for users who already run this behind their own reverse proxy — and if so, how is the "loopback-only,
   unauthenticated" guarantee in [docs/mcp-interface.md](../mcp-interface.md) communicated inside a container
   context where "loopback" no longer means "only this machine" the same way it does on bare metal?
4. Which community directories require an in-repo metadata file versus a purely external listing, and do any of
   them impose licensing or attribution requirements beyond the existing MIT license?
