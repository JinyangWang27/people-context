# Evaluation harness

Fixed tasks, a fictional world, and deterministic scoring for one question: does an agent that can read a
people-context store answer questions about the people in it better than the same agent without one?

Nothing here is packaged. The harness is excluded from the source distribution and was never part of the wheel,
so an installed `people-context` contains no fixtures, prompts, or runner code.

## Run the offline dry run

```bash
uv run python -m evals.harness --runner stub
```

The stub runner replays the recorded answers in `suite/stub-transcripts.json`. It starts no process, opens no
socket, and needs no API key, so it is the check to run after editing a task, a rubric, or the fixture.

## Run against a real agent

```bash
export ANTHROPIC_API_KEY=...        # environment only; never a flag, a file, or the suite
uv run python -m evals.harness --runner claude-cli --out evals/results/<date>-<model>.json
```

Read [docs/evals.md](../docs/evals.md) before recording a result: it documents the conditions, the model ids, what
each rubric measures, and what a published number may and may not claim.

## Layout

| Path | What it holds |
| --- | --- |
| `suite/world.json` | The fictional world, as data a reviewer can read in full |
| `suite/suite.json` | System prompt, the fixed tasks, their rubrics, and runner wiring |
| `suite/stub-transcripts.json` | Hand-written answers for the offline dry run; not model output |
| `harness/` | Loading, world materialization, runners, scoring, and the report |
| `results/` | Dated report documents, one file per recorded run |

Run artifacts land under `<workdir>/artifacts/`; the agent process gets its own empty directory elsewhere.

## Safety properties

- The harness builds its own SQLite store in a throwaway directory and refuses to run against the database the
  local configuration resolves to, or against any database file that already exists.
- `PEOPLE_CONTEXT_DB` and its encryption key are never forwarded to an agent process; the fictional database is
  named on the MCP server command line instead.
- The agent command is an argument vector with `shell=False` and whole-argument placeholders, so a prompt can
  never become an extra flag.
- The agent runs in a fresh empty directory outside the artifacts tree, so the `without_mcp` control cannot read
  `world.db` off disk instead of going through the server, and each `with_mcp` invocation gets its own copy of the
  store so a write tool cannot change what later tasks are scored against.
- The evaluated server is pinned to this checkout rather than resolved from PyPI by name, so a dated result names
  the code it measured.
- Every person, organisation, and event in the fixture is invented, and every address uses the reserved `.test`
  domain.
