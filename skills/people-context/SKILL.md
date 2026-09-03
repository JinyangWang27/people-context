---
name: "people-context"
description: "Install, start, and use the people-context MCP server via stdio or HTTP: resolve people, read context and guidance, capture new knowledge, maintain existing records, and connect the OpenClaw plugin. Covers install, pctx setup, transport, MCP session wiring, and raw tool call patterns."
---

# people-context Skill

## When to use

Any work with the people-context MCP server: install/upgrade, client connection, people/aliases/affiliations/relationships/facts/interactions/reminders — or querying records.

For agent-facing usage patterns see `skills/people-context-usage/`, `skills/remember/`, `skills/reminders/`. This skill covers install, transport, session wiring, and raw tool call patterns only.

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

## 4. MCP session init — Python helper (HTTP only)

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
    if isinstance(parsed, dict) and parsed.get("error") == "validation_error":  # bug #117
        raise RuntimeError(f"{name} validation: {parsed['message']}")
    return res.get("structuredContent") or parsed
```

---

## 5. Resolve identity first — always (for write tools)

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

## 6. `remember` — single-call capture (new in M21)

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

## 7. remember_person + add_alias

```python
call("remember_person", {"name": "Jinyang Wang", "is_self": True,
    "summary": "PhD physicist, AI engineer."})
# Do NOT fold affiliations/facts into summary. Do NOT pass aliases here.
```

`add_alias` — `kind` **must be lowercase** (uppercase silently drops the alias; bug #117):

`kind` values: `native_script` (CJK; add `"script":"Hans"/"Hant"`) · `transliteration` · `nickname` · `handle` · `former_name` · `other`

---

## 8. set_relationship / set_affiliation

```python
call("set_relationship", {"subject_id": pid_a, "object_id": pid_b, "type": "spouse"})
call("set_affiliation",  {"person_id": pid, "org": "Etihad Airways",  # "org", NOT "org_name"
    "role": "Employee", "valid_from": "2026-09-01"})
```

---

## 9. `person` name shortcut on read tools

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

## 10. Staged capture: propose → review → commit

For extracted/inferred content from transcripts, emails, or external sources. **Never auto-commit.**

`aliases` in a `person` candidate are objects, not strings: `{"value": "Al", "kind": "nickname"}`.

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
call("commit_import", {"batch_id": bid, "accept": [cid, ...]})  # user-approved only
```

---

## 11. Record maintenance: correction vs. supersession

| Scenario | Tool |
|---|---|
| Stored value was **wrong** (typo, misheard) | `correct_record` — updates in-place |
| Stored value was **right, world changed** | `supersede_fact` — closes old row, opens replacement atomically |

`supersede_fact` requires `effective_from` within the old fact's validity window. Report the `reason` if refused.

---

## 12. Available tools (post-M21)

**Read-only** (`readOnlyHint=true`):
`resolve_person` · `get_person_context` · `search_people` · `semantic_search` ·
`get_communication_guidance` · `list_reminders` · `get_relationship_graph` ·
`find_connection` · `get_stale_relationships` · `upcoming_dates` · `review_import` ·
`get_person_timeline` · `get_consolidation_context`

**Write**: `remember` · `remember_person` · `add_alias` · `set_relationship` · `set_affiliation` ·
`record_fact` · `record_observation` · `record_trait` · `record_interaction` ·
`set_reminder` · `complete_reminder` · `correct_record` · `supersede_fact` ·
`merge_people` · `forget` · `stage_candidates` · `import_content` · `commit_import` · `set_communication_philosophy`

**Gated** (never suggest enabling to work around a boundary): `get_sensitive_person_context` · `export_data`

---

## 13. Prompts and resources (new in M21)

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

## 14. Known issues (v1.0.x) — bug #117

- `isError` not set on validation errors: `add_alias` with wrong `kind` silently drops the alias. Check body for `{"error":"validation_error"}` — handled in `call()` above.
- `kind` enum missing from JSON schema: use enumerated values in §7.

---

## 15. OpenClaw plugin

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
```

Connects to the opt-in loopback HTTP server (must be running; see §3).
See `docs/openclaw-plugin.md` in the upstream repo for config and security details.
