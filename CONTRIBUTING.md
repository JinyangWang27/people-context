# Contributing

Thank you for helping improve `people-context`. Contributions should preserve the project's local-first privacy
model and its narrow, documented interfaces.

## Report an issue

Use the [public issue tracker](https://github.com/JinyangWang27/people-context/issues) for bugs, feature requests,
documentation problems, and other non-sensitive reports. Include a minimal reproduction, the affected version or
commit, and the expected and observed behavior. Use synthetic data: never attach a real people-context database,
raw imports, credentials, tokens, or personal information.

Suspected vulnerabilities must be reported privately as described in [SECURITY.md](SECURITY.md). Do not disclose
vulnerability details in a public issue, discussion, or pull request before coordinated disclosure.

## Prepare a change

Create a focused branch from current `main`. Keep changes narrowly scoped, add a regression test for every bug,
and update interface or architecture documentation when a public contract changes. Application code follows a
ports-and-adapters architecture:

- `domain/` contains dependency-free entities and business rules;
- `app/` contains use cases and depends only on `domain/` and narrow `ports/` protocols;
- `adapters/` implements persistence, MCP, import, export, and optional semantic integrations; and
- composition belongs in `adapters/runtime.py`, shared by process boundaries such as `cli/` and
  `adapters/mcp/server.py`.

Never import adapters, MCP, or SQLite from `domain/` or `app/`. Preserve the documented disclosure gates, local
storage boundary, loopback-only HTTP policy, and rule that ordinary commands do not access the network.

## Validate Python changes

Install dependencies and run the same primary checks used in CI:

```bash
uv sync --locked --all-extras
uv lock --check
uv run --locked ruff check .
uv run --locked mypy
uv run --locked --with pytest-cov==7.1.0 pytest --cov=people_context -q
uv build
uvx --from twine==7.0.0 twine check dist/*
```

## Validate OpenClaw changes

The OpenClaw package commits its generated `dist/` output. Rebuild it and confirm that no generated delta remains:

```bash
cd openclaw-plugin
npm ci --no-audit --no-fund
npm run build
npm test
npm run plugin:check
npm run plugin:validate
git diff --exit-code -- dist
```

## Validate Obsidian plugin changes

The Obsidian package commits no build output; continuous integration rebuilds it twice from clean lockfile
installations and requires identical checksums. Reproduce it locally with:

```bash
cd obsidian-plugin
npm ci --no-audit --no-fund
npm run typecheck
npm test
npm run build
```

## Submit a pull request

Open a pull request that explains the behavior and privacy impact, links relevant issues, and lists the commands
used for verification. Keep commits green and narrowly scoped with concise imperative Conventional Commit
subjects.

Every pull request requires review by a human other than its author. Address review conversations, and obtain a
fresh approval after the latest reviewable push. Required CI and CodeQL checks must pass before merge.
