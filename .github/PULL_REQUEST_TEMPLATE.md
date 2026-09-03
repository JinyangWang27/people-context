## What and why

<!-- One paragraph. Link the issue: Closes #123 -->

## Privacy impact

<!-- Does this read, write, export, or log personal data differently? "None" is a valid answer. -->

## Contracts touched

- [ ] MCP tool signature, annotation, or response shape (update `docs/mcp-interface.md` and `docs/compatibility.md`)
- [ ] Database schema or migration (update `docs/data-model.md`)
- [ ] CLI command or JSON document (update `docs/cli.md`)
- [ ] None of the above

## Verification

```bash
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -q
```

<!-- Paste anything else you ran, e.g. a manual check against `pctx demo --reset`. -->
