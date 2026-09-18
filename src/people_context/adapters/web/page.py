"""The single document `pctx browse` serves.

Everything the browser executes is in this string: no bundler, no CDN, no font, no third-party
script. Every view is rendered client-side from the JSON endpoints, so there is no second document
to link to and the token never has to be copied into a URL after the first load.

Two rules keep recorded data inert. Every value is placed with `textContent` on elements built by
`createElement` — never `innerHTML` — so a name containing markup is displayed, not parsed. And the
only server-side substitutions are the per-response nonce and the fixed warnings, the latter
HTML-escaped. The page carries no `style` attribute, which the nonce-based policy could not admit.
"""

from __future__ import annotations

import html

_NONCE = "__PCTX_NONCE__"
_REVIEW_WARNING = "__PCTX_REVIEW_WARNING__"
_SOURCES_WARNING = "__PCTX_SOURCES_WARNING__"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>People Context</title>
<style nonce="__PCTX_NONCE__">
:root { color-scheme: light dark; --fg: #1d1d1f; --bg: #fbfbfa; --muted: #6b6b70; --line: #d9d9de;
  --accent: #2f5fb3; --warn-bg: #fff4d6; --warn-fg: #5c4400; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #ececef; --bg: #17171a; --muted: #a0a0a8; --line: #34343a; --accent: #8fb1ff;
    --warn-bg: #3a3017; --warn-fg: #f3dc9c; }
}
body { margin: 0; font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-bottom: 1px solid var(--line); }
header h1 { font-size: 18px; margin: 0; }
nav { display: flex; gap: 8px; flex-wrap: wrap; }
main { padding: 16px; max-width: 960px; margin: 0 auto; }
button { font: inherit; color: var(--fg); background: transparent; border: 1px solid var(--line);
  border-radius: 6px; padding: 4px 10px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button.link { border: none; padding: 0; color: var(--accent); text-align: left; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere; }
th { color: var(--muted); font-weight: 600; }
.warning { background: var(--warn-bg); color: var(--warn-fg); padding: 8px 12px; border-radius: 6px; }
.notice { color: var(--muted); font-style: italic; }
.error { color: #c0392b; }
.pager { display: flex; gap: 8px; margin-top: 12px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
</style>
</head>
<body>
<header>
<h1>People Context</h1>
<nav>
<button type="button" id="nav-people">People</button>
<button type="button" id="nav-sources">Import sources</button>
<button type="button" id="nav-done">Done</button>
</nav>
</header>
<main id="view"></main>
<p id="review-warning" hidden>__PCTX_REVIEW_WARNING__</p>
<p id="sources-warning" hidden>__PCTX_SOURCES_WARNING__</p>
<script nonce="__PCTX_NONCE__">
"use strict";
// The token is read once and the history entry replaced before any request is made, so it never
// stays in the address bar or history. Reloading the token-free URL is refused by the server.
const token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", "/");

const view = document.getElementById("view");
const reviewWarning = document.getElementById("review-warning").textContent;
const sourcesWarning = document.getElementById("sources-warning").textContent;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function button(label, onClick, className) {
  const node = el("button", label, className);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? "?" + text : "";
}

async function api(path, options) {
  const response = await fetch(path, Object.assign({ headers: { "X-Pctx-Token": token }, cache: "no-store" }, options));
  let body = null;
  try { body = await response.json(); } catch (error) { body = null; }
  if (!response.ok) throw new Error(body && body.error ? body.error : "refused (" + response.status + ")");
  return body;
}

function show(title, ...nodes) {
  view.replaceChildren(el("h2", title), ...nodes);
}

function fail(error) {
  view.replaceChildren(el("p", "Error: " + error.message, "error"));
}

function table(headings, rows) {
  const node = el("table");
  const head = el("tr");
  for (const heading of headings) head.append(el("th", heading));
  node.append(head);
  for (const cells of rows) {
    const row = el("tr");
    for (const cell of cells) {
      const td = el("td");
      td.append(cell instanceof Node ? cell : document.createTextNode(cell ?? ""));
      row.append(td);
    }
    node.append(row);
  }
  return node;
}

function list(items) {
  if (!items.length) return el("p", "None recorded.", "notice");
  const node = el("ul");
  for (const item of items) node.append(el("li", item));
  return node;
}

// `back` holds the cursors that opened the earlier pages, so Previous needs no server offset.
function pager(back, cursor, nextCursor, open) {
  const node = el("div", null, "pager");
  if (back.length) node.append(button("Previous", () => open(back[back.length - 1], back.slice(0, -1))));
  if (nextCursor) node.append(button("Next", () => open(nextCursor, back.concat([cursor]))));
  return node;
}

async function showPeople(cursor, back) {
  let doc;
  try { doc = await api("/api/people" + query({ cursor })); } catch (error) { return fail(error); }
  const rows = doc.people.map((person) => [
    button(person.canonical_name, () => showPerson(person.id), "link"),
    person.aliases.join(", "),
    person.summary,
  ]);
  show(
    "People",
    doc.people.length ? table(["Name", "Aliases", "Summary"], rows) : el("p", "No people found.", "notice"),
    pager(back, cursor, doc.next_cursor, showPeople),
  );
}

async function showPerson(personId) {
  let doc;
  try { doc = await api("/api/people/" + encodeURIComponent(personId)); } catch (error) { return fail(error); }
  const identity = el("dl");
  const fields = [
    ["Aliases", doc.person.aliases.join(", ") || "(none)"],
    ["Summary", doc.person.summary || "(none)"],
    ["Context disclosure", doc.disclosure.context],
  ];
  for (const [term, value] of fields) identity.append(el("dt", term), el("dd", value));
  const nodes = [identity];
  if (doc.truncated) {
    nodes.push(el("p", "Facts and interactions are bounded; more exist beyond this view.", "notice"));
  }
  const sections = [
    ["Affiliations", doc.affiliations.map((a) => a.affiliation.role + " at " + a.organization_name)],
    ["Facts", doc.facts.map((fact) => fact.predicate + ": " + fact.value)],
    ["Interactions", doc.interactions.map((i) => String(i.occurred_at).slice(0, 10) + ": " + i.summary)],
    ["Reminders", doc.reminders.map((r) => r.kind + (r.due_at ? " (due " + r.due_at + ")" : "") + ": " + r.text)],
    ["Traits", doc.traits.map((trait) => trait.category + ": " + trait.value)],
  ];
  for (const [title, items] of sections) nodes.push(el("h3", title), list(items));
  nodes.push(button("Back to people", () => showPeople(null, [])));
  show(doc.person.canonical_name, ...nodes);
}

async function showSources(cursor, back) {
  let doc;
  try { doc = await api("/api/sources" + query({ cursor })); } catch (error) { return fail(error); }
  const rows = doc.sources.map((source) => [
    button(source.source_kind, () => showSource(source.id), "link"),
    source.label,
    source.status,
    source.batch_id,
  ]);
  show(
    "Import sources",
    el("p", sourcesWarning, "warning"),
    doc.sources.length ? table(["Kind", "Label", "Status", "Batch"], rows) : el("p", "No import sources.", "notice"),
    pager(back, cursor, doc.next_cursor, showSources),
  );
}

async function showSource(sourceId) {
  let doc;
  try { doc = await api("/api/sources/" + encodeURIComponent(sourceId)); } catch (error) { return fail(error); }
  const details = el("dl");
  const fields = [
    ["Source", doc.source.id],
    ["Kind", doc.source.source_kind],
    ["Label", doc.source.label || "-"],
    ["Status", doc.source.status],
    ["Batch", doc.source.batch_id || "-"],
    ["Staged candidates", doc.staged_total],
  ];
  for (const [term, value] of fields) details.append(el("dt", term), el("dd", value));
  const counts = Object.entries(doc.staged_by_status).map(([status, count]) => status + ": " + count);
  show(
    "Import source",
    el("p", reviewWarning, "warning"),
    details,
    el("h3", "Staged by status"),
    list(counts),
    button("Back to sources", () => showSources(null, [])),
  );
}

async function done() {
  try { await api("/api/done", { method: "POST" }); } catch (error) { return fail(error); }
  view.replaceChildren(el("p", "pctx browse has stopped. You can close this tab."));
}

document.getElementById("nav-people").addEventListener("click", () => showPeople(null, []));
document.getElementById("nav-sources").addEventListener("click", () => showSources(null, []));
document.getElementById("nav-done").addEventListener("click", done);
showPeople(null, []);
</script>
</body>
</html>
"""


def render_page(nonce: str, *, review_warning: str, sources_warning: str) -> str:
    """Return the document with this response's nonce and the escaped warnings filled in."""
    return (
        _TEMPLATE.replace(_REVIEW_WARNING, html.escape(review_warning))
        .replace(_SOURCES_WARNING, html.escape(sources_warning))
        .replace(_NONCE, nonce)
    )
