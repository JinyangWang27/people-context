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
than implicitly creating `1.0.0`, so Conventional Commits alone never reach that milestone. Request an explicit
version with a `Release-As: <version>` footer on a commit merged to `main`.

Release Please also accepts a `release-as` entry in `release-please-config.json`, and this repository
deliberately does not use it. That entry is sticky: it caps the computed version, so once the release it names
ships, `.release-please-manifest.json` can never advance past it and a forgotten entry would silently freeze
every later release while CI stays green. No assertion over repository content can distinguish a stale pin from
a pending one, because the release pull request and the merged result contain identical files. The commit footer
has no such state — the release tag consumes it, and a footer lost during a squash merge fails loudly and
harmlessly, as a release pull request proposing the wrong version.

Release Please maintains one release PR containing the generated changelog and every coupled primary-version
update: `pyproject.toml`, the package `__version__`, Registry metadata, MCPB metadata, Codex plugin metadata,
`uv.lock`, and the release-version assertion used by packaging tests. Feature PRs must not manually bump those
files.

## The 1.0 release

The 1.0 milestone is requested with a `Release-As: 1.0.0` footer, which must survive into the squash commit that
lands on `main`. Put it in the **final paragraph** of the message, alongside any other trailers — a `Release-As:`
line separated from the last block by a blank line is not a git trailer, and `git interpret-trailers` will not
report it. Verify it with `git show -s --format='%(trailers:key=Release-As)' <merge-commit>` after merging. If it
is lost, the next release pull request simply proposes the Conventional-Commit version instead, and the footer
can be supplied again on any later commit. The repository-side preparation for the release is already merged:

- [compatibility.md](compatibility.md) states the MCP, database, CLI, and machine-readable-JSON guarantees that
  the major version makes binding;
- `pyproject.toml` declares `Development Status :: 5 - Production/Stable`;
- `tests/test_packaging_metadata.py` asserts that the five semantic server-release values — the root project
  version, `server.json.version`, the Registry package's pinned `--from` requirement, `mcpb/manifest.json.version`,
  and the `mcpb/pyproject.toml` dependency pin — plus `people_context.__version__` and the locked root project in
  `uv.lock` all carry the same value, whatever Release Please writes;
- `mcpb/manifest.json.manifest_version` stays the MCPB **schema** version and is asserted separately, never set to
  the application release;
- the Registry package is located by `identifier` rather than array position, so an added package entry cannot
  silently move the assertions onto the wrong package.

Version domains that wrap or distribute the server stay explicit rather than accidentally synchronized. The
`.codex-plugin` manifest is deliberately coupled to the primary release and rewritten by Release Please; the
`.claude-plugin`, OpenClaw, and any future Obsidian packages carry their own versions and are bumped on their own
policy. If a 1.0 release intentionally publishes one of those artifacts, synchronize that package's own manifest,
marketplace, lockfile, and documented artifact names together.

The footer leaves no state to clean up after the release. One documentation review does remain: the
"while the project remains below `1.0.0`" note in [compatibility.md](compatibility.md) describes the pre-1.0
minor-bump rule and no longer applies once the major version exists.

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
