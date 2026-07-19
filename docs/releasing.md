# Releasing, PyPI migration, and coverage

## One-time repository setup

### Codecov

Codecov uploads use GitHub OIDC through `codecov/codecov-action`; no long-lived `CODECOV_TOKEN` secret is
required. Ensure the Codecov GitHub App has access to `JinyangWang27/people-context` after the repository rename.
The CI workflow generates `coverage.xml` and uploads it on pushes and same-repository pull requests. Fork pull
requests still run the tests but skip the upload because they do not receive a trusted OIDC context for this
repository.

### PyPI trusted publishing

PyPI project names cannot be renamed. The primary distribution is therefore published as the new project
`people-context`, while the former `people-context-mcp` project receives a small compatibility distribution.
Both projects use Trusted Publishing, so no long-lived PyPI API token is stored in GitHub.

Configure the primary `people-context` project, or a pending publisher before its first release, with:

| Field | Value |
|---|---|
| PyPI project name | `people-context` |
| GitHub owner | `JinyangWang27` |
| GitHub repository | `people-context` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

Keep the legacy `people-context-mcp` project's trusted publisher configured with the same GitHub owner,
repository, workflow filename, and environment. Its final compatibility wheel is built from
`compat/people-context-mcp/` and depends on `people-context>=0.1.0`. It also declares the established
`people-context-mcp` server and `people-context` CLI entry points, which keeps `pip`, `pipx`, and `uv tool`
installations under the old distribution name functional while directing resolution to the new package.

Create a GitHub Actions environment named `pypi`. Requiring approval and limiting deployments to release tags is
recommended. The first successful trusted publication creates `people-context` if the name is still available.

## Distribution and command names

- PyPI distribution: `people-context`.
- Python import package: `people_context` (unchanged).
- MCP server executable: `people-context-mcp` (unchanged for client compatibility).
- Human-operated CLI executable: `people-context` (unchanged).
- Legacy PyPI distribution: `people-context-mcp`, compatibility-only; do not add new implementation code there.

New users should install `people-context`. Existing users may continue to resolve the old distribution, but
project documentation and integrations must use the new distribution name.

## Publish a release

1. Update `project.version` in `pyproject.toml` and merge the change to `main`.
2. Create a GitHub Release from that commit using a matching tag such as `v0.1.0`.
3. Publish the GitHub Release.
4. Approve the `pypi` environment deployments when prompted.

`.github/workflows/release.yml` then:

1. verifies that `uv.lock` matches `pyproject.toml`;
2. builds and checks the `people-context` wheel and source distribution;
3. builds and checks the legacy `people-context-mcp` compatibility wheel and source distribution;
4. publishes the primary artifacts to the `people-context` PyPI project using short-lived OIDC credentials; and
5. publishes the compatibility artifacts to `people-context-mcp` with `skip-existing`, so the fixed migration
   release is uploaded once and harmlessly skipped on later releases.

PyPI release filenames and versions are immutable. If a primary upload partially succeeds, publish a new version
rather than attempting to overwrite existing files. Never delete the legacy project: it reserves the old name and
provides a safe upgrade path.
