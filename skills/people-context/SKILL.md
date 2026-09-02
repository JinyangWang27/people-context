---
name: "people-context"
description: "Install, start, and use the people-context MCP server via HTTP: resolve people, read context and guidance, capture new knowledge through the staged flow, maintain existing records, and connect the OpenClaw plugin. Covers install, server startup, MCP session init, and all tool patterns."
---

# people-context Skill

## When to use

Any work involving the people-context MCP server through the loopback HTTP transport:
installing or upgrading, starting the server, adding people, aliases, affiliations,
relationships, facts, interactions, reminders — or querying existing records.

For agent-facing usage patterns (resolution, guided capture, transcript extraction,
record maintenance) the upstream skills in
`skills/people-context-usage/`, `skills/remember/`, and `skills/reminders/`
hold the canonical rules. This skill handles install, transport, MCP session wiring,
and the raw tool call patterns only.

---

## 1. Install / Upgrade

Prefer `uvx` for zero-install ephemeral runs:

```bash
uvx --from people-context people-context-mcp --http --host 127.0.0.1 --port 8765
```

For a persistent install (recommended when the server runs in background):

```bash
uv tool install people-context          # latest PyPI release
# or latest tag from GitHub:
gh release list --repo JinyangWang27/people-context
uv tool install --force \
  "git+https://github.com/JinyangWang27/people-context.git@<tag>"
```

Verify installed version:

```bash
python3 -c "import people_context; print(people_context.__version__)"
```

---

## 2. Start the server

HTTP transport is **opt-in** and loopback-only. Always bind to `127.0.0.1`.

```bash
# Check if already running before starting
pgrep -fa people-context

# Start in background
nohup people-context-mcp --http --host 127.0.0.1 --port 8765 \
  > /tmp/people-context.log 2>&1 &
sleep 2 && cat /tmp/people-context.log
```

Wait for `Application startup complete` before any tool call.
The startup log prints the active DB path (default:
`~/.local/share/people-context/people-context.db`).

> **Prefer stdio in production.** HTTP is unauthenticated and must be treated as
> accessible to all local processes. Use it for interactive agent sessions; prefer
> the stdio MCP server in automated pipelines.

---

## 3. MCP session init (Python helper)

Every HTTP interaction requires an MCP session. Initialize once, reuse the
`mcp-session-id` header for all subsequent calls in the same script.

```python
import urllib.request, json

BASE    = "http://127.0.0.1:8765/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json, text/event-stream",
}

_id = 0

def _post(payload, session_id=None):
    h = dict(HEADERS)
    if session_id:
        h["mcp-session-id"] = session_id
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(), headers=h, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        sid  = r.headers.get("mcp-session-id")
        body = r.read().decode()
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:]), sid
    return None, sid

# Initialize once — S is the session id for this script run
resp, S = _post({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "levey", "version": "1.0"},
    },
})

_id = 1

def call(name, args):
    """Call any people-context MCP tool. Raises on error or validation failure."""
    global _id
    _id += 1
    r, _ = _post(
        {"jsonrpc": "2.0", "id": _id, "method": "tools/call",
         "params": {"name": name, "arguments": args}},
        S,
    )
    res  = r["result"]
    text = res.get("content", [{}])[0].get("text", "")
    if res.get("isError"):
        raise RuntimeError(f"{name} failed: {text}")
    parsed = json.loads(text) if text else res.get("structuredContent")
    # isError is NOT always set on validation errors (bug #117) — check body too
    if isinstance(parsed, dict) and parsed.get("error") == "validation_error":
        raise RuntimeError(f"{name} validation: {parsed['message']}")
    return res.get("structuredContent") or parsed
```

---

## 4. Resolve identity first — always

All write tools require `person_id` (a ULID string), **never** a name.
Always call `resolve_person` first, and branch on its `ambiguous` boolean —
not on the candidate count.

```python
def resolve(query, hints=None):
    """Returns person_id when resolution is unambiguous, else None."""
    args = {"query": query}
    if hints:
        args["hints"] = hints          # keys: org, role, relationship
    r = call("resolve_person", args)
    if r and not r.get("ambiguous") and r.get("candidates"):
        return r["candidates"][0]["person_id"]
    # ambiguous: surface candidates[]; no person_id: report not found
    return None
```

Hints improve ranking but **do not break exact-score ties**. When resolution
is `ambiguous`, surface the candidate list and let the user choose — never guess.

---

## 5. remember_person

Use for a pure identity assertion (name, summary, `is_self`). Do **not** fold
affiliations, facts, or interactions into the summary — stage those separately.

```python
call("remember_person", {
    "name":     "Jinyang Wang",
    "is_self":  True,
    "summary":  "PhD physicist, AI engineer, maintainer of Awesome Python.",
})
```

Add aliases after creation with `add_alias` (see below). Do not pass `aliases`
to `remember_person`.

---

## 6. add_alias

`kind` **must be lowercase**. An uppercase value silently drops the alias
(bug #117, no error returned).

| Alias type | `kind` | Notes |
|---|---|---|
| Chinese / CJK characters | `native_script` | add `"script": "Hans"` (Simplified) or `"Hant"` (Traditional) |
| Pinyin / romanisation | `transliteration` | |
| Given name / short name | `nickname` | |
| Social / email handle | `handle` | |
| Previous name | `former_name` | |
| Other | `other` | |

```python
pid = resolve("Jinyang Wang")
call("add_alias", {"person_id": pid, "value": "Jinyang",      "kind": "nickname"})
call("add_alias", {"person_id": pid, "value": "汪金洋",        "kind": "native_script", "script": "Hans"})
call("add_alias", {"person_id": pid, "value": "Wāng Jīnyáng", "kind": "transliteration"})
```

---

## 7. set_relationship

Uses `subject_id`, `object_id`, and `type`. The server normalizes synonyms
automatically (e.g. `"spouse"` → `"spouse_of"`, `"parent"` → `"parent_of"`).

```python
call("set_relationship", {
    "subject_id": pid_a,
    "object_id":  pid_b,
    "type":       "spouse",
})
```

---

## 8. set_affiliation

Field is `org` (string), **not** `org_name`. Also requires `role`.
`valid_from` / `valid_to` are optional ISO-8601 dates.

```python
call("set_affiliation", {
    "person_id":  pid,
    "org":        "Etihad Airways",   # "org", not "org_name"
    "role":       "Employee",
    "valid_from": "2026-09-01",
})
```

---

## 9. get_person_context

The person record is under the `"***"` key in the response (not `"person"`).

```python
r       = call("get_person_context", {"person_id": pid})
person  = r["***"]               # ← "***", not "person"
aliases = person.get("aliases", [])
rels    = r.get("relationships", [])
affils  = r.get("affiliations", [])
```

---

## 10. Staged capture: propose → review → commit

Facts, affiliations, and interactions must go through the three-step staged flow.
**Never call `commit_import` automatically** — the commit is an explicit,
user-approved step after review.

### Strict `stage_candidates` vocabulary

| Candidate `type` | Required fields | Optional fields |
|---|---|---|
| `person` | `ref`, `name`, `aliases` (array of objects, may be `[]`) | `summary`, `message_id`, `date` |
| `interaction` | `summary`, `participant_refs`, `date` | `channel`, `message_id`, `sensitivity`, `evidence_ref` |
| `affiliation` | `person_ref`, `org`, `role` | `valid_from`, `valid_to`, `confidence` |
| `fact` | `person_ref`, `predicate`, `value` | `valid_from`, `valid_to`, `confidence`, `sensitivity` |
| `observation` | `person_ref`, `text` | `observed_at`, `sensitivity`, `evidence_ref` |
| `trait` | `person_ref`, `category`, `value`, `evidence_note`, `confidence` | `evidence_refs`, `evidence_ids` |
| `relationship` | `from_ref`, `to_ref`, `relationship_type` | `confidence` |

`aliases` in a `person` candidate are **objects**, not bare strings:
`{"value": "Al", "kind": "nickname"}` — a bare string list is rejected.

### Minimal staged-capture example

```python
call("stage_candidates", {
    "source": "levey-2026-09-02",   # never use raw user text as source
    "candidates": [
        {
            "type": "person",
            "ref":  "jiaxin",
            "name": "Jiaxin",
            "aliases": [{"value": "Jiaxin Wang", "kind": "other"}],
            "summary": "Wife of Jinyang, joining Etihad Airways.",
        },
        {
            "type": "affiliation",
            "person_ref": "jiaxin",
            "org":        "Etihad Airways",
            "role":       "Flight Attendant",
            "valid_from": "2026-08-01",
        },
    ],
})
# → returns batch_id; tell user to review before committing
```

Review and commit (user-approved only):

```python
call("review_import", {"batch_id": batch_id})
call("commit_import", {"batch_id": batch_id, "accept": [candidate_id, ...]})
```

---

## 11. Record maintenance: correction vs. supersession

| Scenario | Right tool |
|---|---|
| Stored value was **wrong** (typo, wrong date, misheard name) | `correct_record` — updates the row in-place |
| Stored value was **right, then the world changed** | `supersede_fact` — closes old row, opens replacement; both commit atomically |

Never use `correct_record` to update a historically correct fact — that erases
the provenance that the old value was ever true.

`supersede_fact` requires `effective_from` to fall within the old fact's validity
window. If the server refuses it with a `reason`, report the reason and ask the user
for the correct date.

---

## 12. Available tools (v1.0.0)

**Read-only** (call freely):
`resolve_person` · `get_person_context` · `search_people` · `semantic_search` ·
`get_communication_guidance` · `list_reminders` · `get_relationship_graph` ·
`find_connection` · `get_stale_relationships` · `upcoming_dates` · `review_import` ·
`get_person_timeline` · `get_consolidation_context`

**Write** (normal approval flow):
`remember_person` · `add_alias` · `set_relationship` · `set_affiliation` ·
`record_fact` · `record_observation` · `record_trait` · `record_interaction` ·
`set_reminder` · `complete_reminder` · `correct_record` · `supersede_fact` ·
`merge_people` · `forget` · `stage_candidates` · `import_content` ·
`commit_import` · `set_communication_philosophy`

**Gated** (not exposed by default; do not suggest enabling to work around a boundary):
`get_sensitive_person_context` · `export_data`

---

## 13. Known issues (v1.0.0) — filed as #117

| Bug | Symptom | Workaround |
|---|---|---|
| `isError` not set on validation errors | `add_alias` with wrong `kind` returns `isError: false` but alias is silently dropped | Check body for `{"error":"validation_error"}` — handled in `call()` helper above |
| `kind` enum missing from JSON schema | Clients can't validate `kind` values upfront | Use the enumerated values in §6 above |

---

## 14. OpenClaw plugin (quick start)

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
openclaw plugins inspect people-context --runtime --json
```

The plugin connects to the opt-in loopback HTTP server, which must be running
separately (see §2). Sensitive-context and export wrappers are not exposed by default.
See `docs/openclaw-plugin.md` in the upstream repo for configuration and security details.
