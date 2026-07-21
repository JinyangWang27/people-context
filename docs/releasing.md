# Releasing and coverage

## One-time repository setup

### Codecov

Codecov uploads use GitHub OIDC through `codecov/codecov-action`; no long-lived `CODECOV_TOKEN` secret is
required. Ensure the Codecov GitHub App has access to `JinyangWang27/people-context`. The CI workflow
generates `coverage.xml` and uploads it on pushes and same-repository pull requests. Fork pull requests still
run the tests but skip the upload because they do not receive a trusted OIDC context for this repository.

### PyPI trusted publishing

Two PyPI projects are published from this repository, both using Trusted Publishing, so no long-lived PyPI
API token is stored in GitHub:

- **`people-context`** — the primary distribution, built from the repository root.
- **`people-context-mcp`** — a compatibility distribution built from `compat/people-context-mcp/`. It
  contains no implementation code: it depends on `people-context>=0.1.0` and declares the same
  `people-context-mcp` server and `people-context` CLI entry points, which keeps `pip`, `pipx`, and
  `uv tool` installations under that distribution name functional while directing resolution to the primary
  package. It also permanently reserves the name.

Both projects' trusted publishers use the same coordinates:

| Field | Value |
|---|---|
| GitHub owner | `JinyangWang27` |
| GitHub repository | `people-context` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

Keep a GitHub Actions environment named `pypi`. Requiring approval and limiting deployments to release tags
is recommended.

## Distribution and command names

- PyPI distribution: `people-context`.
- Python import package: `people_context`.
- MCP server executable: `people-context-mcp`.
- Human-operated CLI executable: `people-context`.
- Compatibility PyPI distribution: `people-context-mcp`; do not add implementation code there.

Documentation and integrations use the `people-context` distribution name; the compatibility distribution
exists only so installations under the other name keep resolving.

## Publish a release

1. Update `project.version` in `pyproject.toml`. In the same commit, update the MCP Registry metadata in
   `server.json` — its top-level `version` and the `--from people-context==<version>` package pin — and, when the
   Registry entry must be (re)published, bump the `version` in `compat/people-context-mcp/pyproject.toml` so the
   marker-bearing compatibility artifact is uploaded rather than skipped. (`tests/test_registry_metadata.py`
   fails if `server.json` drifts from `project.version`.) See [mcp-registry.md](mcp-registry.md) for the full
   Registry publication procedure. Merge the change to `main`.
2. Create a GitHub Release from that commit using a matching tag such as `v0.1.1`.
3. Publish the GitHub Release.
4. Approve the `pypi` environment deployments when prompted.

`.github/workflows/release.yml` then:

1. verifies that `uv.lock` matches `pyproject.toml`;
2. builds and checks the `people-context` wheel and source distribution;
3. builds and checks the `people-context-mcp` compatibility wheel and source distribution;
4. publishes the primary artifacts to the `people-context` PyPI project using short-lived OIDC credentials; and
5. publishes the compatibility artifacts to `people-context-mcp` with `skip-existing`, so the fixed
   compatibility release is uploaded once and harmlessly skipped on later releases.

PyPI release filenames and versions are immutable. If a primary upload partially succeeds, publish a new
version rather than attempting to overwrite existing files. Never delete the compatibility project: it
reserves the name and keeps existing installations resolving.
