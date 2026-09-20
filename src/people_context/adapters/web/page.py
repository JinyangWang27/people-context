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
.badge { display: inline-block; padding: 0 6px; border: 1px solid var(--line); border-radius: 4px; }
.accepted { border-color: var(--accent); color: var(--accent); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; align-items: center; }
.confirm { border: 1px solid var(--accent); border-radius: 6px; padding: 8px 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 4px 0; }
input, select { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 3px 6px; max-width: 100%; }
.edit { border: 1px solid var(--accent); border-radius: 6px; padding: 8px 12px; margin-top: 12px; }
.edit dl { align-items: start; }
.alias { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
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
  if (!response.ok) {
    const refusal = new Error(body && body.error ? body.error : "refused (" + response.status + ")");
    // The use case's own per-field refusals, which carry the field and a fixed message, never a
    // submitted value. Absent on every endpoint but the amendment.
    if (body && Array.isArray(body.fields)) refusal.fields = body.fields;
    throw refusal;
  }
  return body;
}

// Every view and every batch action takes a navigation number before it awaits, and renders only
// if nothing was opened since: a slow response must not paint over the view the user moved to, nor
// carry one batch's result or digest into another.
let navigation = 0;

function navigate() {
  navigation += 1;
  return navigation;
}

function show(title, ...nodes) {
  view.replaceChildren(el("h2", title), ...nodes);
}

function fail(error, at) {
  if (at !== undefined && at !== navigation) return;
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
  const at = navigate();
  let doc;
  try { doc = await api("/api/people" + query({ cursor })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const rows = doc.people.map((person) => [
    button(person.canonical_name, () => showPerson(person.id, cursor, back), "link"),
    person.aliases.join(", "),
    person.summary,
  ]);
  show(
    "People",
    doc.people.length ? table(["Name", "Aliases", "Summary"], rows) : el("p", "No people found.", "notice"),
    pager(back, cursor, doc.next_cursor, showPeople),
  );
}

// `cursor` and `back` are the people page this was opened from, so Back returns to it.
async function showPerson(personId, cursor, back) {
  const at = navigate();
  let doc;
  try { doc = await api("/api/person" + query({ id: personId })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
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
  nodes.push(button("Back to people", () => showPeople(cursor, back)));
  show(doc.person.canonical_name, ...nodes);
}

async function showSources(cursor, back) {
  const at = navigate();
  let doc;
  try { doc = await api("/api/sources" + query({ cursor })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const rows = doc.sources.map((source) => [
    button(source.source_kind, () => showSource(source.id, cursor, back), "link"),
    source.label,
    source.status,
    source.batch_id,
  ]);
  // A batch staged without a receipt is not listed, so it is opened by id.
  const batchInput = el("input");
  batchInput.type = "text";
  batchInput.placeholder = "Batch id";
  batchInput.setAttribute("aria-label", "Batch id");
  const openBatch = el("div", null, "actions");
  const openTyped = () => { if (batchInput.value.trim()) showBatch(batchInput.value.trim()); };
  openBatch.append(batchInput, button("Open batch", openTyped));
  show(
    "Import sources",
    el("p", sourcesWarning, "warning"),
    openBatch,
    doc.sources.length ? table(["Kind", "Label", "Status", "Batch"], rows) : el("p", "No import sources.", "notice"),
    pager(back, cursor, doc.next_cursor, showSources),
  );
}

async function showSource(sourceId, cursor, back) {
  const at = navigate();
  let doc;
  try { doc = await api("/api/source" + query({ id: sourceId })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const details = el("dl");
  const fields = [
    ["Source", doc.source.id],
    ["Kind", doc.source.source_kind],
    ["Label", doc.source.label || "-"],
    ["Status", doc.source.status],
    ["Batch", doc.source.batch_id || "-"],
  ];
  for (const [term, value] of fields) details.append(el("dt", term), el("dd", value));
  const toList = button("Back to sources", () => showSources(cursor, back));
  if (doc.source.redacted) {
    // A forgotten source's counts are withheld, not zero, so none are shown.
    show("Import source", details, el("p", "This source was forgotten; its counts are withheld.", "notice"), toList);
    return;
  }
  details.append(el("dt", "Staged candidates"), el("dd", doc.staged_total));
  const counts = Object.entries(doc.staged_by_status).map(([status, count]) => status + ": " + count);
  const nodes = [el("p", reviewWarning, "warning"), details, el("h3", "Staged by status"), list(counts)];
  if (doc.source.batch_id && doc.staged_total) nodes.push(button("Open batch", () => showBatch(doc.source.batch_id)));
  show("Import source", ...nodes, toList);
}

// Batch review (M30.2). Accepting writes nothing: it only marks rows for the commit, as
// `pctx import review --interactive` does. Withdraw and commit always send the digest of the review
// on screen, so a batch another client changed since is refused with `batch_changed`. After every
// action the batch is read again from the server rather than patched locally.
const batchState = { id: null, review: null, lines: [], accepted: new Set(), message: null, busy: false,
  fields: {}, aliasFields: [], editing: null, commit: null };

// One write at a time: a second click while a withdrawal or commit is in flight would send a
// second request that supersedes the first's result. Every control is disabled synchronously, and
// the re-rendered batch brings fresh ones.
function beginAction() {
  if (batchState.busy) return false;
  batchState.busy = true;
  for (const control of view.querySelectorAll("button, input, select")) control.disabled = true;
  return true;
}

// A refused amendment wrote nothing, so the open form is kept with its messages rather than
// refetched, and the controls it disabled are handed back for the correction.
function reenable() {
  for (const control of view.querySelectorAll("button, input, select")) control.disabled = false;
  if (batchState.commit) batchState.commit.disabled = batchState.accepted.size === 0;
}

function matchState(candidate) {
  if (candidate.type !== "person") return "";
  if (candidate.match_disposition === "ambiguous") {
    return "ambiguous (" + (candidate.match_count ?? "several") + " candidates)";
  }
  return candidate.matched_person_id ? "matches existing person" : "new";
}

// `baseline` is the digest the kept acceptances were made against: the one on screen, or the one
// this page's own withdrawal produced. Any other digest means another client changed the batch.
async function showBatch(batchId, message, baseline) {
  if (batchState.id !== batchId) { batchState.accepted = new Set(); batchState.editing = null; }
  const shown = baseline
    || (batchState.id === batchId && batchState.review ? batchState.review.batch_digest : null);
  batchState.id = batchId;
  batchState.message = message || null;
  const at = navigate();
  let doc;
  try { doc = await api("/api/batch" + query({ id: batchId })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  if (shown !== null && doc.review.batch_digest !== shown && batchState.accepted.size) {
    batchState.accepted = new Set();
    batchState.message = (batchState.message ? batchState.message + " " : "")
      + "Batch changed elsewhere; acceptances discarded.";
  }
  batchState.review = doc.review;
  batchState.lines = doc.lines;
  batchState.fields = doc.fields || {};
  batchState.aliasFields = doc.alias_fields || [];
  const pending = new Set(doc.review.candidates.filter((row) => row.status === "pending").map((row) => row.id));
  for (const id of batchState.accepted) if (!pending.has(id)) batchState.accepted.delete(id);
  // A row another client committed or withdrew is no longer editable, and the use case would
  // refuse the edit; the form closes rather than inviting one.
  if (batchState.editing !== null && !pending.has(batchState.editing)) batchState.editing = null;
  renderBatch();
}

function renderBatch() {
  const review = batchState.review;
  const counts = {};
  for (const row of review.candidates) counts[row.status] = (counts[row.status] || 0) + 1;
  const header = el("dl");
  header.append(el("dt", "Batch"), el("dd", review.batch_id));
  for (const [status, count] of Object.entries(counts)) header.append(el("dt", status), el("dd", count));
  const boxes = [];
  const rows = review.candidates.map((row, index) => {
    let pick = "";
    if (row.status === "pending") {
      pick = el("input");
      pick.type = "checkbox";
      pick.setAttribute("aria-label", "Select #" + row.ordinal);
      boxes.push([pick, row.id]);
    }
    const status = el("span");
    status.append(el("span", row.status, "badge"));
    if (batchState.accepted.has(row.id)) status.append(" ", el("span", "accepted", "badge accepted"));
    const verbatim = el("details");
    verbatim.append(el("summary", "Candidate"), el("pre", JSON.stringify(row.candidate, null, 2)));
    // Only a pending row opens a form: the use case refuses an amendment of any other with
    // `candidate_not_pending`, and the page does not offer what would be refused.
    const edit = row.status === "pending"
      ? button(batchState.editing === row.id ? "Close" : "Edit", () => toggleEdit(row.id))
      : "";
    return [pick, "#" + row.ordinal, status, batchState.lines[index], matchState(row.candidate), verbatim, edit];
  });
  const selected = () => boxes.filter(([box]) => box.checked).map(([, id]) => id);
  const confirmArea = el("div");
  const actions = el("div", null, "actions");
  actions.append(
    button("Accept selected", () => acceptSelected(selected())),
    button("Withdraw selected", () => withdrawSelected(selected())),
  );
  const commit = button("Commit accepted", () => confirmCommit(confirmArea));
  commit.disabled = batchState.accepted.size === 0;
  batchState.commit = commit;
  actions.append(commit, el("span", batchState.accepted.size + " accepted", "notice"));
  const nodes = [header, el("p", reviewWarning, "warning")];
  if (batchState.message) nodes.push(el("p", batchState.message, "notice"));
  const editing = review.candidates.find((row) => row.id === batchState.editing && row.status === "pending");
  nodes.push(
    actions,
    confirmArea,
    table(["", "#", "Status", "Candidate", "Match", "Staged", ""], rows),
    editing ? editForm(editing) : el("div"),
    button("Back to sources", () => showSources(null, [])),
  );
  show("Import batch", ...nodes);
}

// Opening a form reads nothing from the server: it is built from the batch already on screen and
// the field lists that came with it, so one row at a time is open and closing one loses nothing.
function toggleEdit(candidateId) {
  batchState.editing = batchState.editing === candidateId ? null : candidateId;
  renderBatch();
}

// --- M30.3 inline edit ------------------------------------------------------------------------
// The form is generated from the candidate type's declared field list, which the batch response
// carries, so a field left unset at staging still has an input. Saving sends only the fields whose
// control moved: the amendment merge is shallow, so a field the patch does not name keeps its
// stored value and a collection the patch does name replaces the stored one outright. Nothing
// here validates — every rule is the use case's, and a refusal is shown against the field it named.

function seedText(value) {
  return value === undefined || value === null ? "" : String(value);
}

// An empty control means "no value", which is what a patch says with null; the use case refuses
// it on a field that requires one, naming that field.
function submitted(raw, numeric) {
  if (raw === "") return null;
  if (!numeric) return raw;
  const parsed = Number(raw);
  return Number.isNaN(parsed) ? raw : parsed;
}

function textControl(descriptor, staged) {
  const input = el("input");
  input.type = descriptor.control === "date" ? "date" : descriptor.control === "number" ? "number" : "text";
  if (descriptor.control === "number") {
    input.min = descriptor.min;
    input.max = descriptor.max;
    input.step = descriptor.step;
  }
  const seed = seedText(staged);
  input.value = seed;
  input.setAttribute("aria-label", descriptor.name);
  const numeric = descriptor.control === "number";
  return { node: input, read: () => (input.value === seed ? undefined : submitted(input.value, numeric)) };
}

function optionList(select, options, seed) {
  const blank = el("option", "(unset)");
  blank.value = "";
  select.append(blank);
  // A stored value the current vocabulary no longer offers stays on the list rather than being
  // silently rewritten to nothing by an edit to some other field.
  const known = options.some((option) => option.value === seed);
  for (const option of seed !== "" && !known ? [{ value: seed, label: seed }, ...options] : options) {
    const node = el("option", option.label);
    node.value = option.value;
    select.append(node);
  }
}

function selectControl(descriptor, staged, options) {
  const select = el("select");
  const seed = seedText(staged);
  optionList(select, options, seed);
  select.value = seed;
  select.setAttribute("aria-label", descriptor.name);
  return { node: select, read: () => (select.value === seed ? undefined : submitted(select.value, false)) };
}

// Every row of the batch whose type this reference may name — withdrawn and committed included,
// because the use case accepts those as targets, and no row of any other batch, because the page
// only ever holds one.
function referenceOptionsFor(descriptor) {
  const options = [];
  batchState.review.candidates.forEach((row, index) => {
    if (descriptor.targets.includes(row.candidate.type)) {
      options.push({ value: row.id, label: "#" + row.ordinal + " " + batchState.lines[index] });
    }
  });
  return options;
}

function multiControl(descriptor, staged) {
  const select = el("select");
  select.multiple = true;
  const chosen = Array.isArray(staged) ? staged : [];
  const options = referenceOptionsFor(descriptor);
  select.size = Math.min(Math.max(options.length, 2), 8);
  for (const option of options) {
    const node = el("option", option.label);
    node.value = option.value;
    node.selected = chosen.includes(option.value);
    select.append(node);
  }
  select.setAttribute("aria-label", descriptor.name);
  const same = (values) => values.length === chosen.length && values.every((value) => chosen.includes(value));
  return {
    node: select,
    read: () => {
      const values = Array.from(select.selectedOptions).map((option) => option.value);
      return same(values) ? undefined : values;
    },
  };
}

// `evidence_ids` names durable records outside this batch, so there is nothing to pick from here;
// the stored ids are shown with the command that changes them.
function readonlyControl(descriptor, staged, row) {
  const values = Array.isArray(staged) ? staged : [];
  const node = el("div");
  node.append(el("p", values.length ? values.join(", ") : "(none)"));
  node.append(el("p", "Change with: pctx import amend " + batchState.id + " " + row.id
    + " --patch " + JSON.stringify({ [descriptor.name]: values }), "notice"));
  return { node, read: () => undefined };
}

function aliasControl(descriptor, staged) {
  const stagedRows = Array.isArray(staged) ? staged : [];
  const container = el("div");
  const rows = [];

  const addRow = (base) => {
    const entry = { base, controls: [], node: el("div", null, "alias") };
    for (const field of batchState.aliasFields) {
      const control = field.control === "select"
        ? selectControl(field, base[field.name], field.options.map((value) => ({ value, label: value })))
        : textControl(field, base[field.name]);
      entry.controls.push([field.name, control]);
      entry.node.append(el("span", field.name, "notice"), control.node);
    }
    entry.node.append(button("Remove", () => {
      entry.removed = true;
      container.removeChild(entry.node);
    }));
    rows.push(entry);
    container.append(entry.node);
  };

  for (const alias of stagedRows) addRow(alias);
  const adder = button("Add alias", () => addRow({}));
  const node = el("div");
  node.append(container, adder);

  // An untouched row is re-emitted from what was staged, key for key, so rebuilding the list to
  // change one alias never drops another alias's language or script.
  const rebuild = () => rows.filter((entry) => !entry.removed).map((entry) => {
    const alias = Object.assign({}, entry.base);
    for (const [name, control] of entry.controls) {
      const value = control.read();
      if (value === undefined) continue;
      if (value === null) delete alias[name];
      else alias[name] = value;
    }
    return alias;
  });
  return {
    node,
    read: () => {
      const rebuilt = rebuild();
      return JSON.stringify(rebuilt) === JSON.stringify(stagedRows) ? undefined : rebuilt;
    },
  };
}

function buildControl(descriptor, row) {
  const staged = row.candidate[descriptor.name];
  if (descriptor.control === "alias_list") return aliasControl(descriptor, staged);
  if (descriptor.control === "readonly") return readonlyControl(descriptor, staged, row);
  if (descriptor.control === "candidate_multiselect") return multiControl(descriptor, staged);
  if (descriptor.control === "candidate_select") {
    return selectControl(descriptor, staged, referenceOptionsFor(descriptor));
  }
  if (descriptor.control === "select") {
    return selectControl(descriptor, staged, descriptor.options.map((value) => ({ value, label: value })));
  }
  return textControl(descriptor, staged);
}

// The ambiguity picker. `match_candidates` is the review's own projection of the people this name
// resolves to; choosing one is an ordinary `matched_person_id` patch, which the use case accepts
// only when the matcher itself produced that person.
function matchPicker(row, rowErrors) {
  const matches = row.match_candidates || [];
  const select = el("select");
  const unresolved = el("option", "(leave unresolved)");
  unresolved.value = "";
  select.append(unresolved);
  for (const match of matches) {
    const option = el("option", match.canonical_name + (match.name_truncated ? " (name shortened)" : "")
      + " - " + match.id);
    option.value = match.id;
    select.append(option);
  }
  select.value = seedText(row.candidate.matched_person_id);
  select.setAttribute("aria-label", "Matching person");
  const typed = el("input");
  typed.type = "text";
  typed.setAttribute("aria-label", "Person id");
  typed.placeholder = "Person id";
  const nodes = [el("p", "This name matches several people. Choose one, or leave it unresolved."), select];
  if (row.match_candidates_truncated) {
    nodes.push(
      el("p", "More people share this name than are listed; find the right one with pctx search or the "
        + "resolve_person tool and paste its id.", "notice"),
      typed,
    );
  }
  nodes.push(button("Choose", () => {
    const chosen = typed.value.trim() || select.value || null;
    return amendRow(row, { matched_person_id: chosen }, [], rowErrors, "Recorded the match for #" + row.ordinal + ".");
  }));
  const node = el("div");
  node.append(...nodes);
  return node;
}

function editForm(row) {
  const descriptors = (batchState.fields || {})[row.candidate.type] || [];
  const controls = [];
  const grid = el("dl");
  for (const descriptor of descriptors) {
    const control = buildControl(descriptor, row);
    control.slot = el("div");
    controls.push([descriptor.name, control]);
    const value = el("dd");
    value.append(control.node, control.slot);
    grid.append(el("dt", descriptor.name), value);
  }
  const rowErrors = el("div");
  const panel = el("div", null, "edit");
  panel.append(el("h3", "Editing #" + row.ordinal + " (" + row.candidate.type + ")"));
  if (row.match_candidates) panel.append(matchPicker(row, rowErrors));
  panel.append(grid, rowErrors);
  const actions = el("div", null, "actions");
  actions.append(
    button("Save", () => saveEdit(row, controls, rowErrors)),
    button("Cancel", () => toggleEdit(row.id)),
  );
  panel.append(actions);
  return panel;
}

function saveEdit(row, controls, rowErrors) {
  const patch = {};
  for (const [name, control] of controls) {
    const value = control.read();
    if (value !== undefined) patch[name] = value;
  }
  if (!Object.keys(patch).length) {
    rowErrors.replaceChildren(el("p", "Nothing changed.", "notice"));
    return;
  }
  return amendRow(row, patch, controls, rowErrors, "Amended #" + row.ordinal + ".");
}

async function amendRow(row, patch, controls, rowErrors, message) {
  if (!beginAction()) return;
  const at = navigate();
  const batchId = batchState.id;
  let result;
  try {
    result = await api("/api/batch/amend", {
      method: "POST",
      headers: { "X-Pctx-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({
        batch_id: batchId,
        candidate_id: row.id,
        patch,
        expected_batch_digest: batchState.review.batch_digest,
      }),
    });
  } catch (error) {
    batchState.busy = false;
    if (at !== navigation) return;
    showRefusal(error, controls, rowErrors);
    return reenable();
  }
  batchState.busy = false;
  batchState.editing = null;
  if (at === navigation) return showBatch(batchId, message, result.batch_digest);
}

// One message per field, against the control that caused it. A refusal the use case located at
// the whole candidate, or at a field it would not name, belongs to the row instead.
function showRefusal(error, controls, rowErrors) {
  const named = new Map();
  for (const entry of error.fields || []) named.set(entry.field, entry.message);
  const shown = new Set();
  for (const [name, control] of controls) {
    control.slot.replaceChildren();
    if (!named.has(name)) continue;
    control.slot.append(el("p", named.get(name), "error"));
    shown.add(name);
  }
  const rest = [el("p", "Refused: " + error.message, "error")];
  for (const entry of error.fields || []) {
    if (!shown.has(entry.field)) rest.push(el("p", entry.field + ": " + entry.message, "error"));
  }
  rowErrors.replaceChildren(...rest);
}

function acceptSelected(ids) {
  for (const id of ids) batchState.accepted.add(id);
  return showBatch(batchState.id, ids.length ? "Accepted " + ids.length + "; nothing is committed yet." : null);
}

async function batchAction(path, ids) {
  return api(path, {
    method: "POST",
    headers: { "X-Pctx-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify({
      batch_id: batchState.id,
      candidate_ids: ids,
      expected_batch_digest: batchState.review.batch_digest,
    }),
  });
}

async function withdrawSelected(ids) {
  if (!ids.length || !beginAction()) return;
  const at = navigate();
  const batchId = batchState.id;
  let message;
  let baseline;
  try {
    const result = await batchAction("/api/batch/withdraw", ids);
    message = "Withdrew " + result.withdrawn + ".";
    baseline = result.batch_digest;
  } catch (error) { message = "Refused: " + error.message; } finally { batchState.busy = false; }
  if (at === navigation) return showBatch(batchId, message, baseline);
}

// One explicit click, on a confirmation naming how many were accepted. The result may commit
// fewer: an ambiguous person, or a row that resolves through one, is reported unresolved.
function confirmCommit(area) {
  const count = batchState.accepted.size;
  const panel = el("div", null, "confirm");
  panel.append(
    el("p", "Commit " + count + " accepted candidates? Unresolved rows stay pending."),
    button("Confirm commit of " + count, () => commitAccepted()),
    " ",
    button("Cancel", () => area.replaceChildren()),
  );
  area.replaceChildren(panel);
}

async function commitAccepted() {
  if (!batchState.accepted.size || !beginAction()) return;
  const at = navigate();
  const batchId = batchState.id;
  const ids = Array.from(batchState.accepted);
  batchState.accepted = new Set();
  let message;
  try {
    const result = await batchAction("/api/batch/commit", ids);
    message = "Committed " + result.committed_ids.length + "; " + result.unresolved_ids.length
      + " unresolved; " + result.skipped_ids.length + " already committed.";
  } catch (error) { message = "Refused: " + error.message; } finally { batchState.busy = false; }
  if (at === navigation) return showBatch(batchId, message);
}

async function done() {
  // Stopping is a navigation too: no pending view or batch response may repaint after it.
  const at = navigate();
  try { await api("/api/done", { method: "POST" }); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
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
