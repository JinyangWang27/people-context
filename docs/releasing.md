# Releasing and coverage

## One-time repository setup

### Codecov

Codecov uploads use GitHub OIDC through `codecov/codecov-action`; no long-lived `CODECOV_TOKEN` secret is
required. Ensure the Codecov GitHub App has access to `JinyangWang27/people-context`. The CI workflow
generates `coverage.xml` and uploads it on pushes and same-repository pull requests. Fork pull requests still
run the tests but skip the upload because they do not receive a trusted OIDC context for this repository.

### Release Please

Release Please runs from `.github/workflows/release-please.yml` with the repository-scoped `GITHUB_TOKEN`; no
personal access token or long-lived release credential is stored. In **Settings > Actions > General**, enable
**Allow GitHub Actions to create and approve pull requests** so the workflow can maintain its release PR.

GitHub suppresses workflows that would normally be triggered by a `GITHUB_TOKEN`-created pull request or tag.
After Release Please creates or updates its pull request, the same workflow therefore dispatches CI, CodeQL, and
each validation workflow affected by the synchronized version files at the release PR branch. When it creates a
release, it likewise dispatches the PyPI and Docker publication workflows at the new tag. This preserves the
repository-token boundary without adding a broader credential.

### PyPI trusted publishing

One PyPI project is published from this repository using Trusted Publishing, so no long-lived PyPI API token is
stored in GitHub:

- **`people-context`** — the primary distribution, built from the repository root.

Its trusted publisher uses these coordinates:

| Field             | Value            |
| ----------------- | ---------------- |
| GitHub owner      | `JinyangWang27`  |
| GitHub repository | `people-context` |
| Workflow filename | `release.yml`    |
| Environment name  | `pypi`           |

Keep a GitHub Actions environment named `pypi`. Requiring approval and limiting deployments to release tags
is recommended.

## Distribution and command names

- PyPI distribution: `people-context`.
- Python import package: `people_context`.
- MCP server executables: `people-context` (Registry/package-aligned) and `people-context-mcp` (compatibility alias).
- Human-operated CLI executable: `pctx`.

Documentation and integrations use the `people-context` distribution name.

## Prepare changes for a release

Use concise Conventional Commit squash titles on pull requests merged to `main`:

- `fix:` proposes a patch release;
- `feat:` proposes a minor release;
- `feat!:` or a `BREAKING CHANGE:` footer proposes a breaking release.

While the project remains below `1.0.0`, breaking changes are configured to advance the minor version rather
than implicitly creating `1.0.0`. Use a `Release-As: 1.0.0` footer when the project deliberately reaches that
milestone. Other explicit versions can use the same footer.

Release Please maintains one release PR containing the generated changelog and every coupled primary-version
update: `pyproject.toml`, the package `__version__`, Registry metadata, MCPB metadata, Codex plugin metadata,
`uv.lock`, and the release-version assertion used by packaging tests. Feature PRs must not manually bump those
files.

## Publish a release

1. Review the Release Please PR, including the proposed SemVer change, changelog, and synchronized metadata.
2. Approve its pending workflow runs and wait for required CI and CodeQL checks.
3. Merge the Release Please PR when the accumulated changes are ready to publish.
4. The next Release Please run creates the matching `vX.Y.Z` tag and published GitHub Release.
5. That same workflow dispatches `.github/workflows/release.yml` and
   `.github/workflows/docker-publish.yml` at the newly created tag. The dispatches are used deliberately because
   `GITHUB_TOKEN`-created tags and releases do not start another workflow.
6. Approve the `pypi` environment deployment when prompted.

`.github/workflows/release.yml` then:

1. rejects branch-based dispatches and requires a `v*` tag ref;
2. verifies that `uv.lock` matches `pyproject.toml`;
3. builds and checks the `people-context` wheel and source distribution;
4. publishes the primary artifacts to PyPI using short-lived OIDC credentials; and
5. builds and attaches the matching native-UV MCPB bundle after PyPI publication succeeds.

The workflow retains its `release.published` trigger for manually created releases, and `workflow_dispatch` is
also available for a deliberate retry from an existing release tag. Manual retries tolerate PyPI files that
already exist so a failed downstream MCPB build or attachment can complete; release-triggered publication still
fails loudly on duplicate filenames.

PyPI release filenames and versions are immutable. If an upload partially succeeds, publish a new version rather
than attempting to overwrite existing files.
