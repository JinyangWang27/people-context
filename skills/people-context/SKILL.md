---
name: "people-context"
description: "Install, start, and use the people-context MCP server via stdio or HTTP: resolve people, read context and guidance, capture new knowledge, maintain existing records, and connect the OpenClaw plugin. Covers install, pctx setup, transport, MCP session wiring, and raw tool call patterns."
---

# people-context Skill

## When to use

Any work with the people-context MCP server: install/upgrade, client connection, people/aliases/affiliations/relationships/facts/interactions/reminders — or querying records.

For agent-facing usage patterns see `skills/people-context-usage/`, `skills/remember/`, `skills/reminders/`. This skill covers install, transport, session wiring, and raw tool call patterns only.

---

## Shortest working path

Pick the first route that works and stay on it for the whole session:

1. **Native MCP tools.** If `people-context` tools are already listed in your client, call them directly.
   Their schemas are in context: do not re-list tools, read server source, or restart the connection to
   rediscover them.
2. **The installed CLI.** Otherwise use `pctx`, which opens the same store with no bridge:

   | Task | Command |
   |---|---|
   | Add an alias | `pctx add-alias "Mei Tanaka" Mimi --kind nickname` |
   | Fix or drop a wrong alias | `pctx update-alias "Mei Tanaka" mimi --kind nickname` · `pctx remove-alias "Mei Tanaka" mei@example.com` |
   | One direct statement | `pctx remember "Dana" "prefers short emails"` |
   | Staged import | `pctx import stage-candidates --source <label> --input candidates.json --json` → `pctx import review <batch> --json` → `pctx import commit <batch> --accept 1-7 --json` (or `--all`) |

3. **Raw HTTP.** Only when neither exists; see the appendix.

Every route keeps the same safeguards: resolve before writing, surface ambiguity, and commit extracted
material only after the user accepts it. Report a compact receipt — changed ids, statuses, unresolved
items, errors — rather than the full response, and read one representation of each result, never both
the text and the structured copy.

---

## 1. Install / Upgrade

```bash
uv tool install people-context          # latest PyPI release
uv tool install --force \
  "git+https://github.com/JinyangWang27/people-context.git@<tag>"
```

Zero-install: `uvx --from people-context people-context-mcp` (stdio) or add `--http --host 127.0.0.1 --port 8765`.

---

## 2. Connect an MCP client — `pctx setup`

```bash
pctx setup claude-desktop   # writes/merges stdio entry into Claude Desktop config
pctx setup cursor           # same for Cursor
pctx setup windsurf         # same for Windsurf
pctx setup vscode           # uses "servers" key with "type": "stdio"
pctx setup claude-code      # drives: claude mcp add
pctx setup codex            # drives: codex mcp add
```

Backs up existing config, writes atomically, refuses symlinks and invalid JSON. `--dry-run` to preview.
`pctx init` offers `pctx setup` at a TTY on first run.

---

## 3. Transport

**stdio (default/preferred).** JSON-RPC on stdin/stdout; `pctx setup` wires this. No port/firewall concern.

**HTTP (opt-in).** `--http --host 127.0.0.1 --port 8765`. Unauthenticated — interactive sessions only.
Wait for `Application startup complete` in the log before any tool call.

---

## 4. Resolve identity first — always (for write tools)

Examples from here on write `call(name, args)` for one tool call, whichever route makes it.

Write tools require `person_id` (ULID), never a name. Branch on `ambiguous`:

```python
r = call("resolve_person", {"query": "Alice Ng", "hints": {"org": "Acme"}})
if not r.get("ambiguous") and r.get("candidates"):
    c = r["candidates"][0]
    if not c.get("match_reason", "").startswith("fuzzy"):  # fuzzy = treat as ambiguous
        pid = c["person_id"]
```

When `ambiguous=true` or `match_reason` starts with `"fuzzy"`: surface candidates, confirm — never guess.

---

## 5. `remember` — single-call capture (new in M21)

Resolves, optionally creates, and records one statement in one audited transaction. Use for direct
statements in conversation. **Not for extracted/inferred material from transcripts** — use staged capture.

```python
call("remember", {"person": "Alice Ng", "note": "prefers short emails", "org": "Acme", "role": "CTO"})
call("remember", {"person": "Bob",      "kind": "affiliation", "org": "Etihad", "role": "Fleet Manager"})
call("remember", {"person": "Jiaxin",   "relationship": "spouse"})
```

Check `status` — **nothing is written when status ≠ `recorded`**:

| `status` | Meaning |
|---|---|
| `recorded` | `r["recorded"]` lists `{kind, id, summary}` |
| `ambiguous` | Several close candidates — `r["candidates"]` |
| `unconfirmed` | One fuzzy-only match — confirm before retrying |
| `no_self` | Relationship needs a self record (none exists) |
| `nothing_to_record` | Bare name, no note/org/relationship |
| `invalid_request` | Structural mismatch — checked before any write |

`kind: auto` classifies `note` by a fixed keyword table. `org` and `relationship` record their own rows
whatever `kind` says. All rows share one `transaction_id`; any failure rolls back everything.

---

## 6. remember_person + add_alias

```python
call("remember_person", {"name": "Jinyang Wang", "is_self": True,
    "summary": "PhD physicist, AI engineer."})
# Do NOT fold affiliations/facts into summary. Do NOT pass aliases here.
```

`add_alias` — `kind` is a lowercase enum published in the tool schema; an unlisted value (`"OTHER"`) is refused, not recorded:

`kind` values: `native_script` (CJK; add `"script":"Hans"/"Hant"`) · `transliteration` · `nickname` · `handle` · `former_name` · `other`

An email address is an alias only with `kind: "handle"`; any other kind is refused. A wrong alias is fixed with `update_alias` (any of `new_value`, `kind`, `lang`, `script`) or dropped with `remove_alias`; both name the alias by value. Never `forget` or `merge_people` to fix a name.

---

## 7. set_relationship / set_affiliation

```python
call("set_relationship", {"subject_id": pid_a, "object_id": pid_b, "type": "spouse"})
call("set_affiliation",  {"person_id": pid, "org": "Etihad Airways",  # "org", NOT "org_name"
    "role": "Employee", "valid_from": "2026-09-01"})
```

---

## 8. `person` name shortcut on read tools

`get_person_context`, `get_communication_guidance`, `list_reminders`, `get_relationship_graph`,
`get_person_timeline`, `get_consolidation_context`, and `upcoming_dates` accept `person` (name string)
alongside `person_id`. Saves the resolve round-trip when the name is unambiguous.

On error, returns a structured payload (not an exception):
`{"error": "ambiguous_person"|"unconfirmed_person"|"person_not_found"|"missing_person", "candidates": [...]}`

### get_person_context

```python
r = call("get_person_context", {"person_id": pid, "include_communication": True})
person = r["***"]           # ← "***", not "person"
# r["truncated"] == True means facts/interactions budget was hit
```

---

## 9. Staged capture: propose → review → commit

For extracted/inferred content from transcripts, emails, or external sources. **Never auto-commit.**

Put every candidate from one source in one `stage_candidates` call. `aliases` on a `person` is optional;
when present, entries are objects, not strings: `{"value": "Al", "kind": "nickname"}`. `label` needs `source_kind`.

```python
bid = call("stage_candidates", {
    "source": "levey-2026-09-03",
    "candidates": [
        {"type": "person", "ref": "jiaxin", "name": "Jiaxin",
         "aliases": [{"value": "Jiaxin Wang", "kind": "other"}]},
        {"type": "affiliation", "person_ref": "jiaxin",
         "org": "Etihad Airways", "role": "Flight Attendant", "valid_from": "2026-08-01"},
    ],
})["batch_id"]

call("review_import", {"batch_id": bid})            # read-only, no approval needed
r = call("commit_import", {"batch_id": bid, "accepted_ids": [cid, ...]})  # user-approved only
```

The commit receipt is the verification: ids in `committed_ids` are stored. Read records back only for ids
in `unresolved_ids` or `skipped_ids`, or when the user asks to see them.

---

## 10. Record maintenance: correction vs. supersession

| Scenario | Tool |
|---|---|
| Stored value was **wrong** (typo, misheard) | `correct_record` — updates in-place |
| Stored value was **right, world changed** | `supersede_fact` — closes old row, opens replacement atomically |

`supersede_fact` requires `effective_from` within the old fact's validity window. Report the `reason` if refused.

---

## 11. Available tools (post-M21)

**Read-only** (`readOnlyHint=true`):
`resolve_person` · `get_person_context` · `search_people` · `semantic_search` ·
`get_communication_guidance` · `list_reminders` · `get_relationship_graph` ·
`find_connection` · `get_stale_relationships` · `upcoming_dates` · `review_import` ·
`get_person_timeline` · `get_consolidation_context`

**Write**: `remember` · `remember_person` · `add_alias` · `update_alias` · `remove_alias` · `set_relationship` · `set_affiliation` ·
`record_fact` · `record_observation` · `record_trait` · `record_interaction` ·
`set_reminder` · `complete_reminder` · `correct_record` · `supersede_fact` ·
`merge_people` · `forget` · `stage_candidates` · `import_content` · `commit_import` · `set_communication_philosophy`

**Gated** (never suggest enabling to work around a boundary): `get_sensitive_person_context` · `export_data`

---

## 12. Prompts and resources (new in M21)

| Kind | Name / URI | Purpose |
|---|---|---|
| resource | `people-context://guide` | Full usage skill body |
| resource | `people-context://self` | User's own record, or `{"found": false}` |
| prompt | `who(name)` | Resolve, then read context on confident match |
| prompt | `remember(statement)` | `remember` for direct statements; `stage_candidates` for extracted |
| prompt | `meeting_prep(attendees)` | Context + guidance per attendee; read-only |
| prompt | `end_of_session_capture()` | Propose via `stage_candidates`; never commit |
| prompt | `maintenance_review(name)` | Timeline + consolidation signals |

---

## 13. OpenClaw plugin

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
```

Connects to the opt-in loopback HTTP server (must be running; see §3).
See `docs/openclaw-plugin.md` in the upstream repo for config and security details.

---

## Appendix: raw HTTP bridge (last resort)

Only when neither native tools nor `pctx` exist. Open one session and reuse it for every call;
`call` returns one representation (`structuredContent`, else the parsed text) — print a receipt, not
the envelope.

```python
import urllib.request, json

BASE = "http://127.0.0.1:8765/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_id = 0

def _post(payload, session_id=None):
    h = {**HEADERS, **({"mcp-session-id": session_id} if session_id else {})}
    req = urllib.request.Request(BASE, json.dumps(payload).encode(), h, method="POST")
    with urllib.request.urlopen(req) as r:
        sid = r.headers.get("mcp-session-id")
        for line in r.read().decode().splitlines():
            if line.startswith("data:"): return json.loads(line[5:]), sid
    return None, sid

resp, S = _post({"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"levey","version":"1.0"}}})
_id = 1

def call(name, args):
    global _id; _id += 1
    r, _ = _post({"jsonrpc":"2.0","id":_id,"method":"tools/call","params":{"name":name,"arguments":args}}, S)
    res = r["result"]; text = res.get("content",[{}])[0].get("text","")
    if res.get("isError"): raise RuntimeError(f"{name} failed: {text}")
    parsed = json.loads(text) if text else res.get("structuredContent")
    if isinstance(parsed, dict) and parsed.get("error"):  # servers <= 1.1.0 left isError unset
        raise RuntimeError(f"{name} refused: {parsed.get('message', parsed['error'])}")
    return res.get("structuredContent") or parsed
```
