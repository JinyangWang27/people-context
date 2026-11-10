"""The single document `pctx browse` serves.

Everything the browser executes is in this string: no bundler, no CDN, no font, no third-party
script. Every view is rendered client-side from the JSON endpoints, so there is no second document
to link to and the token never has to be copied into a URL after the first load.

Two rules keep recorded data inert. Every value is placed with `textContent` on elements built by
`createElement` — never `innerHTML` — so a name containing markup is displayed, not parsed. And the
only server-side substitutions are the per-response nonce, the language the page opens in, and
the interface text of `i18n.py` with the fixed warnings, as JSON whose `<`, `>`, and `&` are
escaped so nothing in it can close the script. The page carries no `style` attribute, which the
nonce-based policy could not admit.

Every sentence goes through `t(key, params)`, which substitutes `{name}` placeholders in one pass,
so a recorded value placed into a sentence is never itself searched for placeholders. The 中文 / EN
button switches language without a request and redraws the current view; the language a launch
opens in is the operator's (`--lang`, the environment, or config.toml), and the page stores none.
"""

from __future__ import annotations

import json

from people_context.adapters.web.i18n import FIELD_LABELS, MESSAGES, VALUE_LABELS, WARNINGS

_NONCE = "__PCTX_NONCE__"
_LANG = "__PCTX_LANG__"
_I18N = "__PCTX_I18N__"

_TEMPLATE = """<!doctype html>
<html lang="__PCTX_LANG__">
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
th { color: var(--muted); font-weight: 600; white-space: nowrap; }
.warning { background: var(--warn-bg); color: var(--warn-fg); padding: 8px 12px; border-radius: 6px; }
.notice { color: var(--muted); font-style: italic; }
.error { color: #c0392b; }
.pager { display: flex; gap: 8px; margin-top: 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.filters input[type=search] { flex: 1 1 16rem; min-width: 0; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
.badge { display: inline-block; padding: 0 6px; border: 1px solid var(--line); border-radius: 4px;
  white-space: nowrap; }
.accepted { border-color: var(--accent); color: var(--accent); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; align-items: center; }
.confirm { border: 1px solid var(--accent); border-radius: 6px; padding: 8px 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 4px 0; }
input, select, textarea { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 3px 6px; max-width: 100%; }
textarea { width: 100%; box-sizing: border-box; }
.edit { border: 1px solid var(--accent); border-radius: 6px; padding: 8px 12px; margin-top: 12px; }
.edit dl { align-items: start; }
.alias { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
a { color: var(--accent); overflow-wrap: anywhere; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible,
summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.group { margin-top: 24px; }
.group h3 { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; margin: 0 0 4px;
  overflow-wrap: anywhere; }
.proposal dl { margin: 4px 0 0; }
.row-actions { display: flex; flex-wrap: wrap; gap: 4px; }
@media (max-width: 640px) {
  table.review, table.review tr, table.review td { display: block; width: 100%; box-sizing: border-box; }
  table.review th { display: none; }
  table.review tr { border-bottom: 1px solid var(--line); padding: 6px 0; }
  table.review td { border: none; padding: 2px 0; }
  dl { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<header>
<h1 id="app-title">People Context</h1>
<nav>
<button type="button" id="nav-people">People</button>
<button type="button" id="nav-sources">Import sources</button>
<button type="button" id="nav-done">Close</button>
<button type="button" id="nav-lang"></button>
</nav>
</header>
<main id="view"></main>
<script nonce="__PCTX_NONCE__">
"use strict";
// The token is read once and the history entry replaced before any request is made, so it never
// stays in the address bar or history. Reloading the token-free URL is refused by the server.
const token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", "/");

const view = document.getElementById("view");

// --- Language ---------------------------------------------------------------------------------
// The interface text of every offered language, fixed for this launch, and the language on screen.
const I18N = __PCTX_I18N__;
let lang = I18N.messages[I18N.default] ? I18N.default : "en";

// One pass over the template: a value placed into a sentence is never searched for placeholders.
function t(key, params) {
  const text = (I18N.messages[lang] || {})[key] ?? I18N.messages.en[key] ?? key;
  if (!params) return text;
  return text.replace(/[{]([a-z_]+)[}]/g, (whole, name) => (name in params ? String(params[name]) : whole));
}

// A declared field or an enum value as this language labels it. English shows the name itself,
// so `fallback` keeps what the page showed before there was a choice of language.
function fieldLabel(name, fallback) {
  return (I18N.fields[lang] || {})[name] || (fallback === undefined ? name : fallback);
}

function valueLabel(value) {
  return (I18N.values[lang] || {})[value] || value;
}

// A control's accessible name: the declared field in English, as the page has always named it,
// and its label in any other language.
function fieldAria(name) {
  return lang === "en" ? name : fieldLabel(name);
}

function warning(which) {
  return (I18N.warnings[lang] || {})[which] || I18N.warnings.en[which];
}

// What the toggle redraws: the view on screen, from what it already holds where it can.
let current = null;

// The toggle names the language it switches to, in that language.
const LANGUAGE_NAMES = { en: "EN", "zh-CN": "中文" };

function applyLanguage() {
  if (document.documentElement) document.documentElement.lang = lang;
  document.title = t("app.title");
  document.getElementById("app-title").textContent = t("app.title");
  document.getElementById("nav-people").textContent = t("nav.people");
  document.getElementById("nav-sources").textContent = t("nav.sources");
  document.getElementById("nav-done").textContent = t("nav.done");
  const toggle = document.getElementById("nav-lang");
  const other = I18N.languages.find((code) => code !== lang) || lang;
  toggle.textContent = LANGUAGE_NAMES[other] || other;
  toggle.setAttribute("aria-label", t("nav.language"));
  toggle.setAttribute("lang", other);
}

function switchLanguage() {
  const other = I18N.languages.find((code) => code !== lang);
  if (!other) return;
  lang = other;
  applyLanguage();
  if (current) current();
}

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
    // An alias conflict's fixed reason code (`canonical_name` or `duplicate_name`), never a value.
    if (body && typeof body.reason === "string") refusal.reason = body.reason;
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
  view.replaceChildren(el("p", t("error", { message: error.message }), "error"));
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
  if (!items.length) return el("p", t("common.none_recorded"), "notice");
  const node = el("ul");
  for (const item of items) node.append(el("li", item));
  return node;
}

// `back` holds the cursors that opened the earlier pages, so Previous needs no server offset.
function pager(back, cursor, nextCursor, open) {
  const node = el("div", null, "pager");
  if (back.length) node.append(button(t("pager.previous"), () => open(back[back.length - 1], back.slice(0, -1))));
  if (nextCursor) node.append(button(t("pager.next"), () => open(nextCursor, back.concat([cursor]))));
  return node;
}

// The people list's search, order, and filters. Kept across views, so Back from a person returns to
// the same narrowed list; changing any of them starts again from the first page.
const peopleFilter = { q: "", sort: "name", organization: "", group: "" };

let restoringHistory = false;
let viewerClosed = false;

function rememberPage(page, args) {
  if (restoringHistory || viewerClosed) return;
  const state = { pctx: true, page, args, filters: { ...peopleFilter } };
  if (JSON.stringify(history.state) === JSON.stringify(state)) return;
  if (history.state && history.state.pctx) history.pushState(state, "");
  else history.replaceState(state, "");
}

const PEOPLE_SORTS = ["name", "created", "updated"];

function peopleControls(filters) {
  const box = el("div", null, "filters");
  const refilter = (key, value) => {
    if (peopleFilter[key] === value) return;
    peopleFilter[key] = value;
    showPeople(null, []);
  };
  const search = el("input");
  search.type = "search";
  search.value = peopleFilter.q;
  search.placeholder = t("people.search.placeholder");
  search.setAttribute("aria-label", t("people.search.aria"));
  // `change` fires on Enter and when the box loses focus, so typing does not query per keystroke.
  search.addEventListener("change", () => refilter("q", search.value.trim()));
  box.append(search);
  const picker = (key, label, options, blank) => {
    const select = el("select");
    select.setAttribute("aria-label", label);
    if (blank) {
      const any = el("option", blank);
      any.value = "";
      select.append(any);
    }
    for (const option of options) {
      const node = el("option", option.label);
      node.value = option.value;
      select.append(node);
    }
    // A filter whose choice is no longer offered still shows it rather than silently widening.
    if (peopleFilter[key] && !options.some((option) => option.value === peopleFilter[key])) {
      const kept = el("option", t("people.filter.kept"));
      kept.value = peopleFilter[key];
      select.append(kept);
    }
    select.value = peopleFilter[key];
    select.addEventListener("change", () => refilter(key, select.value));
    box.append(select);
  };
  picker("sort", t("people.sort.aria"), PEOPLE_SORTS.map((value) => ({ value, label: t("people.sort." + value) })),
    null);
  const named = (rows) => rows.map((row) => ({ value: row.id, label: row.name }));
  if ((filters.organizations || []).length || peopleFilter.organization) {
    picker("organization", t("people.filter.organization.aria"), named(filters.organizations || []),
      t("people.filter.organization.any"));
  }
  if ((filters.groups || []).length || peopleFilter.group) {
    picker("group", t("people.filter.group.aria"), named(filters.groups || []), t("people.filter.group.any"));
  }
  return box;
}

async function showPeople(cursor, back) {
  rememberPage("people", [cursor, back]);
  current = () => showPeople(cursor, back);
  const at = navigate();
  let doc;
  try { doc = await api("/api/people" + query({ cursor, ...peopleFilter })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const organizations = doc.organization_names || {};
  const rows = doc.people.map((person) => {
    const emails = person.emails || [];
    return [
      button(person.display_name || person.canonical_name, () => showPerson(person.id, cursor, back), "link"),
      person.full_name || person.canonical_name,
      (organizations[person.id] || []).join(", "),
      // Email addresses are contact information, shown on the person view rather than as names here.
      person.aliases.filter((alias) => !emails.includes(alias)).join(", "),
      person.summary,
    ];
  });
  const narrowed = peopleFilter.q || peopleFilter.organization || peopleFilter.group;
  show(
    t("people.title"),
    peopleControls(doc.filters || {}),
    doc.people.length
      ? table(["preferred_name", "name", "organization", "aliases", "summary"].map(
        (column) => t("people.column." + column)), rows)
      : el("p", t(narrowed ? "people.none_match" : "people.none"), "notice"),
    pager(back, cursor, doc.next_cursor, showPeople),
  );
}

// A refusal arrives as a code; the text for it is this language's, and an unknown code is shown as is.
function aliasRefusal(error) {
  const code = error.reason || error.message;
  const key = "refusal." + code;
  return t("refused", { reason: key in I18N.messages.en ? t(key) : error.message });
}

function aliasLabel(alias) {
  const tags = [valueLabel(alias.kind), alias.lang, alias.script].filter((tag) => tag);
  return alias.value + " (" + tags.join(", ") + ")";
}

const ALIAS_CONTROLS = "button, input, select, textarea";

// One alias write: every control on the view is disabled until it settles, and success reopens the
// person so the page shows what is stored rather than what it sent. `notice` may turn a successful
// reply into a message instead, for a write that changed nothing.
async function aliasWrite(path, body, message, reopen, notice) {
  for (const control of view.querySelectorAll(ALIAS_CONTROLS)) control.disabled = true;
  // As for batch actions: a reply that lands after the user moved on must not repaint the old view.
  const at = navigate();
  let result;
  try {
    result = await api(path, {
      method: "POST",
      headers: { "X-Pctx-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (at !== navigation) return;
    message.textContent = aliasRefusal(error);
    for (const control of view.querySelectorAll(ALIAS_CONTROLS)) control.disabled = false;
    return;
  }
  if (at !== navigation) return;
  const said = notice ? notice(result) : null;
  if (said) {
    message.textContent = said;
    for (const control of view.querySelectorAll(ALIAS_CONTROLS)) control.disabled = false;
    return;
  }
  reopen();
}

// A new alias, through the same write and reload as an edit. Under Contact the kind is fixed to
// `handle`, the only kind an email address may have as an alias.
function aliasAdder(personId, kinds, reopen, contact) {
  const row = el("div", null, "alias");
  const message = el("span", null, "error");
  function idle() {
    row.replaceChildren(button(t(contact ? "alias.add_email" : "alias.add"), adding), message);
  }
  function adding() {
    message.textContent = "";
    const input = (label) => {
      const node = el("input");
      node.placeholder = label;
      node.setAttribute("aria-label", t("alias.new.aria", { label }));
      return node;
    };
    const value = input(t(contact ? "alias.placeholder.email" : "alias.placeholder.value"));
    const lang = input(t("alias.placeholder.language"));
    const script = input(t("alias.placeholder.script"));
    const kind = el("select");
    for (const option of kinds) {
      const node = el("option", valueLabel(option));
      node.value = option;
      kind.append(node);
    }
    kind.value = contact ? "handle" : "nickname";
    kind.setAttribute("aria-label", t("alias.new.kind.aria"));
    const save = () => {
      if (!value.value.trim()) {
        message.textContent = t("alias.enter_value");
        return;
      }
      const body = { person_id: personId, value: value.value, kind: kind.value };
      if (lang.value.trim()) body.lang = lang.value;
      if (script.value.trim()) body.script = script.value;
      aliasWrite("/api/person/alias/add", body, message, reopen,
        (result) => (result.added ? null : t("alias.already_stored")));
    };
    const fields = contact ? [value] : [value, kind, lang, script];
    row.replaceChildren(...fields, button(t("common.add"), save), button(t("common.cancel"), idle), message);
  }
  idle();
  return row;
}

function aliasRow(personId, alias, kinds, reopen) {
  const row = el("div", null, "alias");
  const message = el("span", null, "error");
  const target = { person_id: personId, value: alias.value };
  const remove = () => aliasWrite("/api/person/alias/remove", target, message, reopen);
  function idle() {
    row.replaceChildren(el("span", aliasLabel(alias)), button(t("common.edit"), editing),
      button(t("common.remove"), confirming), message);
  }
  function confirming() {
    message.textContent = "";
    const prompt = el("span", t("alias.remove_prompt", { value: alias.value }));
    row.replaceChildren(prompt, button(t("common.remove"), remove), button(t("common.cancel"), idle), message);
  }
  function editing() {
    message.textContent = "";
    const input = (seed, label) => {
      const node = el("input");
      node.value = seed || "";
      node.placeholder = label;
      node.setAttribute("aria-label", t("alias.aria", { label }));
      return node;
    };
    const value = input(alias.value, t("alias.placeholder.value"));
    const lang = input(alias.lang, t("alias.placeholder.language"));
    const script = input(alias.script, t("alias.placeholder.script"));
    const kind = el("select");
    for (const option of kinds) {
      const node = el("option", valueLabel(option));
      node.value = option;
      kind.append(node);
    }
    kind.value = alias.kind;
    kind.setAttribute("aria-label", t("alias.kind.aria"));
    const save = () => {
      // Only changed, non-blank fields are sent: the update leaves anything absent as it was.
      const body = { person_id: personId, value: alias.value };
      if (value.value.trim() && value.value !== alias.value) body.new_value = value.value;
      if (kind.value !== alias.kind) body.kind = kind.value;
      if (lang.value.trim() && lang.value !== (alias.lang || "")) body.lang = lang.value;
      if (script.value.trim() && script.value !== (alias.script || "")) body.script = script.value;
      if (Object.keys(body).length === 2) return idle();
      aliasWrite("/api/person/alias/update", body, message, reopen);
    };
    row.replaceChildren(value, kind, lang, script, button(t("common.save"), save), button(t("common.cancel"), idle),
      message);
  }
  idle();
  return row;
}

// Email addresses are contact information, so they are edited under Contact rather than among names.
function aliasEditor(personId, doc, reopen, contact) {
  const emails = doc.person.emails || [];
  const details = (doc.alias_details || []).filter((alias) => emails.includes(alias.value) === contact);
  if (!details.length) return el("span", t("common.none"));
  const box = el("div");
  for (const alias of details) box.append(aliasRow(personId, alias, doc.alias_kinds || [], reopen));
  return box;
}

// The record types the person view can remove, through the same `Forget` as the `forget` MCP tool.
const REMOVABLE = new Set(["affiliation", "fact", "interaction"]);

// One stored record with an Edit form built from the fields `CorrectRecord` accepts for its type.
// Saving corrects the record in place — fixing what was recorded wrongly, with the before and after
// kept in the audit log — and sends only the fields that moved; an emptied field is sent as null.
// A removable type also has Remove, which asks first: removal is a forget, so the record is deleted
// and its history redacted rather than kept, and an interaction goes for every participant.
function recordRow(type, record, label, descriptors, reopen) {
  const row = el("li");
  const message = el("span", null, "error");
  const seed = (name) => (name === "valid_from" || name === "valid_to" ? (record.period || {})[name] : record[name]);
  function idle() {
    message.textContent = "";
    const edit = button(t("common.edit"), editing);
    edit.setAttribute("aria-label", t("record.edit.aria", { label }));
    const controls = [edit];
    if (REMOVABLE.has(type)) {
      const remove = button(t("common.remove"), confirming);
      remove.setAttribute("aria-label", t("record.remove.aria", { label }));
      controls.push(" ", remove);
    }
    row.replaceChildren(el("span", label), " ", ...controls, message);
  }
  function confirming() {
    message.textContent = "";
    const shared = (record.participant_ids || []).length > 1;
    const prompt = el("span", t(shared ? "record.remove.prompt_shared" : "record.remove.prompt", { label }));
    const remove = button(t("common.remove"), () =>
      aliasWrite("/api/record/remove", { entity_type: type, entity_id: record.id }, message, reopen));
    remove.setAttribute("aria-label", t("record.remove.confirm.aria", { label }));
    row.replaceChildren(prompt, " ", remove, button(t("common.cancel"), idle), message);
  }
  function editing() {
    message.textContent = "";
    const grid = el("dl");
    const controls = [];
    for (const descriptor of descriptors) {
      const control = descriptor.control === "select"
        ? selectControl(descriptor, seed(descriptor.name), enumOptions(descriptor))
        : textControl(descriptor, seed(descriptor.name));
      controls.push([descriptor.name, control]);
      const value = el("dd");
      value.append(control.node);
      grid.append(el("dt", fieldLabel(descriptor.name, descriptor.name.split("_").join(" "))), value);
    }
    const save = () => {
      const fields = {};
      for (const [name, control] of controls) {
        const value = control.read();
        if (value !== undefined) fields[name] = value;
      }
      if (!Object.keys(fields).length) return idle();
      aliasWrite("/api/record/correct", { entity_type: type, entity_id: record.id, fields }, message, reopen);
    };
    row.replaceChildren(el("span", label), grid, button(t("common.save"), save), button(t("common.cancel"), idle),
      message);
  }
  idle();
  return row;
}

// A form that records one new record for the person. `fields` are [key, label, type, required],
// the label a key of `adder.`; text is sent trimmed, and an optional field left blank is not sent.
function recordAdder(personId, noun, path, fields, reopen) {
  const row = el("div", null, "alias");
  const message = el("span", null, "error");
  function idle() {
    row.replaceChildren(button(t("adder." + noun), adding), message);
  }
  function adding() {
    message.textContent = "";
    const inputs = [];
    const nodes = [];
    for (const [key, name, type, required] of fields) {
      const label = t("adder." + name);
      const node = el("input");
      node.type = type;
      node.placeholder = label;
      node.setAttribute("aria-label", t("adder." + noun + ".aria", { label }));
      inputs.push([key, node, required]);
      if (type === "date") nodes.push(el("span", label));
      nodes.push(node);
    }
    const save = () => {
      if (inputs.some(([, node, required]) => required && !node.value.trim())) {
        message.textContent = t("adder." + noun + ".missing");
        return;
      }
      const body = { person_id: personId };
      for (const [key, node] of inputs) if (node.value.trim()) body[key] = node.value.trim();
      aliasWrite(path, body, message, reopen);
    };
    row.replaceChildren(...nodes, button(t("common.add"), save), button(t("common.cancel"), idle), message);
  }
  idle();
  return row;
}

// A new role at an organization, through the same `SetAffiliation` as the `set_affiliation` MCP
// tool: an organization is matched by name, case- and accent-insensitively, and created if none
// matches. Dates are optional; an exact repeat of a stored affiliation records nothing new.
function affiliationAdder(personId, reopen) {
  return recordAdder(personId, "affiliation", "/api/person/affiliation/add", [
    ["org", "organization", "text", true],
    ["role", "role", "text", true],
    ["valid_from", "from", "date", false],
    ["valid_to", "to", "date", false],
  ], reopen);
}

// A new fact, through the same `RecordFact` as the `record_fact` MCP tool, of ordinary sensitivity.
function factAdder(personId, reopen) {
  return recordAdder(personId, "fact", "/api/person/fact/add", [
    ["predicate", "predicate", "text", true],
    ["value", "value", "text", true],
    ["valid_from", "from", "date", false],
    ["valid_to", "to", "date", false],
  ], reopen);
}

// A new interaction with this person, through the same `RecordInteraction` as the
// `record_interaction` MCP tool. A summary only, never a transcript; without a date it is now.
function interactionAdder(personId, reopen) {
  return recordAdder(personId, "interaction", "/api/person/interaction/add", [
    ["summary", "summary", "text", true],
    ["channel", "channel", "text", false],
    ["occurred_on", "on", "date", false],
  ], reopen);
}

// Optional name components and summary, edited through the same `EditPerson` as `pctx edit`.
function personFieldRow(personId, field, stored, reopen, pendingEdits) {
  const row = el("div", null, "alias");
  const message = el("span", null, "error");
  function idle() {
    pendingEdits.delete(field);
    message.textContent = "";
    const edit = button(t("common.edit"), () => editing());
    edit.setAttribute("aria-label", t("person.edit_" + field));
    row.replaceChildren(el("span", stored || t("common.none")), " ", edit, message);
  }
  function editing(draft) {
    message.textContent = "";
    const control = textControl({ name: field, control: "text" }, stored || "", draft);
    pendingEdits.set(field, () => control.node.value);
    const save = () => {
      const value = control.read();
      if (value === undefined) return idle();
      aliasWrite("/api/person/edit", { person_id: personId, [field]: value === null ? "" : value }, message, () => {
        pendingEdits.delete(field);
        reopen();
      });
    };
    row.replaceChildren(control.node, button(t("common.save"), save), button(t("common.cancel"), idle), message);
  }
  const readDraft = pendingEdits.get(field);
  if (readDraft) editing(readDraft());
  else idle();
  return row;
}

function recordList(type, entries, descriptors, reopen) {
  if (!entries.length) return el("p", t("common.none_recorded"), "notice");
  const node = el("ul");
  for (const [record, label] of entries) node.append(recordRow(type, record, label, descriptors, reopen));
  return node;
}

// `cursor` and `back` are the people page this was opened from, so Back returns to it.
async function showPerson(personId, cursor, back, previousNameDetails = null, pendingEdits = new Map()) {
  rememberPage("person", [personId, cursor, back]);
  current = () => showPerson(personId, cursor, back, previousNameDetails, pendingEdits);
  const at = navigate();
  let doc;
  try { doc = await api("/api/person" + query({ id: personId })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const identity = el("dl");
  const reopen = () => showPerson(personId, cursor, back, details, pendingEdits);
  const aliases = el("dd");
  const kinds = doc.alias_kinds || [];
  aliases.append(aliasEditor(doc.person.id, doc, reopen, false), aliasAdder(doc.person.id, kinds, reopen, false));
  const contact = el("dd");
  contact.append(aliasEditor(doc.person.id, doc, reopen, true), aliasAdder(doc.person.id, kinds, reopen, true));
  const preferred = el("dd");
  preferred.append(personFieldRow(doc.person.id, "preferred_name", doc.person.preferred_name, reopen, pendingEdits));
  identity.append(el("dt", t("person.preferred_name")), preferred,
    el("dt", t("person.name")), el("dd", doc.person.full_name || doc.person.canonical_name));
  const details = el("details");
  details.open = Boolean(previousNameDetails && previousNameDetails.open);
  previousNameDetails = null;
  current = reopen;
  const components = el("dl");
  for (const field of ["given_name", "family_name"]) {
    const value = el("dd");
    value.append(personFieldRow(doc.person.id, field, doc.person[field], reopen, pendingEdits));
    components.append(el("dt", t("person." + field)), value);
  }
  const orderLabel = el("label", t("person.family_name_first"));
  const order = el("input");
  order.type = "checkbox";
  order.checked = Boolean(doc.person.family_name_first);
  const orderError = el("span", null, "error");
  order.addEventListener("change", () => aliasWrite("/api/person/edit",
    { person_id: doc.person.id, family_name_first: order.checked }, orderError, reopen));
  orderLabel.append(order);
  details.append(el("summary", t("person.name_details")), components, orderLabel, orderError,
    el("p", t("person.name_fallback"), "notice"));
  const metadata = el("dl");
  aliases.append(el("p", t("person.alias_help"), "notice"));
  const summary = el("dd");
  summary.append(personFieldRow(doc.person.id, "summary", doc.person.summary, reopen, pendingEdits));
  metadata.append(el("dt", t("person.aliases")), aliases,
    el("dt", t("person.contact")), contact, el("dt", t("person.summary")), summary,
    el("dt", t("person.disclosure")), el("dd", valueLabel(doc.disclosure.context)));
  const nodes = [identity, details, metadata];
  if (doc.truncated) {
    nodes.push(el("p", t("person.truncated"), "notice"));
  }
  const forms = doc.record_fields || {};
  const editable = (type, entries) => recordList(type, entries, forms[type] || [], reopen);
  nodes.push(
    el("h3", t("person.affiliations")),
    editable("affiliation", doc.affiliations.map((a) => [a.affiliation,
      t("person.affiliation_label", { role: a.affiliation.role, organization: a.organization_name })])),
    affiliationAdder(doc.person.id, reopen),
    el("h3", t("person.facts")),
    editable("fact", doc.facts.map((fact) => [fact, fact.predicate + ": " + fact.value])),
    factAdder(doc.person.id, reopen),
    el("h3", t("person.interactions")),
    editable("interaction", doc.interactions.map((i) => [i, String(i.occurred_at).slice(0, 10)
      + (i.channel ? " (" + i.channel + ")" : "") + ": " + i.summary])),
    interactionAdder(doc.person.id, reopen),
    el("h3", t("person.reminders")),
    editable("reminder", doc.reminders.map((r) => [r,
      valueLabel(r.kind) + (r.due_at ? t("person.reminder_due", { date: r.due_at }) : "") + ": " + r.text])),
    el("h3", t("person.traits")),
    editable("trait", doc.traits.map((trait) => [trait, valueLabel(trait.category) + ": " + trait.value])),
  );
  nodes.push(button(t("person.back"), () => showPeople(cursor, back)));
  show(doc.person.display_name || doc.person.canonical_name, ...nodes);
}

async function showSources(cursor, back) {
  rememberPage("sources", [cursor, back]);
  current = () => showSources(cursor, back);
  const at = navigate();
  let doc;
  try { doc = await api("/api/sources" + query({ cursor })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const rows = doc.sources.map((source) => [
    button(source.source_kind, () => showSource(source.id, cursor, back), "link"),
    source.label,
    valueLabel(source.status),
    source.batch_id,
  ]);
  // A batch staged without a receipt is not listed, so it is opened by id.
  const batchInput = el("input");
  batchInput.type = "text";
  batchInput.placeholder = t("sources.batch_id");
  batchInput.setAttribute("aria-label", t("sources.batch_id"));
  const openBatch = el("div", null, "actions");
  const openTyped = () => { if (batchInput.value.trim()) showBatch(batchInput.value.trim()); };
  openBatch.append(batchInput, button(t("sources.open_batch"), openTyped));
  const columns = ["kind", "label", "status", "batch"].map((column) => t("sources.column." + column));
  show(
    t("sources.title"),
    el("p", warning("sources"), "warning"),
    openBatch,
    doc.sources.length ? table(columns, rows) : el("p", t("sources.none"), "notice"),
    pager(back, cursor, doc.next_cursor, showSources),
  );
}

async function showSource(sourceId, cursor, back) {
  rememberPage("source", [sourceId, cursor, back]);
  current = () => showSource(sourceId, cursor, back);
  const at = navigate();
  let doc;
  try { doc = await api("/api/source" + query({ id: sourceId })); } catch (error) { return fail(error, at); }
  if (at !== navigation) return;
  const details = el("dl");
  const fields = [
    [t("source.source"), doc.source.id],
    [t("sources.column.kind"), doc.source.source_kind],
    [t("sources.column.label"), doc.source.label || "-"],
    [t("sources.column.status"), valueLabel(doc.source.status)],
    [t("sources.column.batch"), doc.source.batch_id || "-"],
  ];
  for (const [term, value] of fields) details.append(el("dt", term), el("dd", value));
  const toList = button(t("sources.back"), () => showSources(cursor, back));
  if (doc.source.redacted) {
    // A forgotten source's counts are withheld, not zero, so none are shown.
    show(t("source.title"), details, el("p", t("source.forgotten"), "notice"), toList);
    return;
  }
  details.append(el("dt", t("source.staged")), el("dd", doc.staged_total));
  const counts = Object.entries(doc.staged_by_status).map(([status, count]) => valueLabel(status) + ": " + count);
  const nodes = [el("p", warning("review"), "warning"), details, el("h3", t("source.by_status")), list(counts)];
  if (doc.source.batch_id && doc.staged_total) {
    nodes.push(button(t("sources.open_batch"), () => showBatch(doc.source.batch_id)));
  }
  show(t("source.title"), ...nodes, toList);
}

// Batch review (M30.2). Accepting writes nothing: it only marks rows for the commit, as
// `pctx import review --interactive` does. Withdraw and commit always send the digest of the review
// on screen, so a batch another client changed since is refused with `batch_changed`. After every
// action the batch is read again from the server rather than patched locally.
const batchState = { id: null, review: null, lines: [], accepted: new Set(), message: null, busy: false,
  fields: {}, aliasFields: [], editing: null, commit: null, form: null };

// One write at a time: a second click while a withdrawal or commit is in flight would send a
// second request that supersedes the first's result. Every control is disabled synchronously, and
// the re-rendered batch brings fresh ones.
const _CONTROLS = "button, input, select, textarea";

function beginAction() {
  if (batchState.busy) return false;
  batchState.busy = true;
  for (const control of view.querySelectorAll(_CONTROLS)) control.disabled = true;
  return true;
}

// A refused amendment wrote nothing, so the open form is kept with its messages rather than
// refetched, and the controls it disabled are handed back for the correction.
function reenable() {
  for (const control of view.querySelectorAll(_CONTROLS)) control.disabled = false;
  if (batchState.commit) batchState.commit.disabled = batchState.accepted.size === 0;
}

// Whether a person row creates someone, joins someone already stored, or owes a decision first.
function identityState(row) {
  const candidate = row.candidate;
  if (candidate.type !== "person") return "";
  if (candidate.match_disposition === "ambiguous") {
    return t("identity.ambiguous", { count: candidate.match_count ?? t("identity.several") });
  }
  if (row.name_is_email) return t("identity.email");
  return t(candidate.matched_person_id ? "identity.existing" : "identity.new");
}

// The person a row is about: a person row heads its own group, and any other row joins the first
// staged person its person references name. The references are read from the field list rather
// than named here, so a new one groups without a change to this page.
function personOf(row, people) {
  if (row.candidate.type === "person") return row.id;
  for (const descriptor of batchState.fields[row.candidate.type] || []) {
    if (!(descriptor.targets || []).includes("person")) continue;
    const value = row.candidate[descriptor.name];
    for (const id of Array.isArray(value) ? value : [value]) if (people.has(id)) return id;
  }
  return null;
}

// Only an absolute http(s) address becomes a link, so a `javascript:` or other scheme in staged text
// stays inert. The page already sends `Referrer-Policy: no-referrer`; `rel` repeats it per link. A
// value carrying a label before its address, as a stored source does (`import/agent:` then a URL),
// links the address and keeps the label as text.
function textOrLink(text) {
  // Built, not written out: the page carries no literal address, so a test can hold it to none.
  const at = Math.max(...["https", "http"].map((scheme) => text.indexOf(scheme + "://")));
  let url = null;
  try { url = at < 0 ? null : new URL(text.slice(at)); } catch (error) { url = null; }
  if (!url || (url.protocol !== "http:" && url.protocol !== "https:")) return el("span", text);
  const link = el("a", text.slice(at));
  link.href = url.href;
  link.rel = "noopener noreferrer";
  link.target = "_blank";
  if (at === 0) return link;
  const node = el("span", null);
  node.append(el("span", text.slice(0, at)), link);
  return node;
}

// A row named by what it proposes, without the ids the CLI line carries.
function rowLabel(row) {
  const candidate = row.candidate;
  const what = candidate.name
    || (candidate.predicate ? candidate.predicate + " = " + candidate.value : null)
    || candidate.summary || candidate.text || candidate.relationship_type || candidate.org || candidate.value || "";
  return "#" + row.ordinal + " " + valueLabel(candidate.type) + (what ? ": " + what : "");
}

// The staged candidate as labelled fields instead of JSON. Empty fields are left out, as is a
// reference to the person whose group the row already sits in.
function proposal(row, owner, showSource) {
  const node = el("div", null, "proposal");
  node.append(el("strong", valueLabel(row.candidate.type)));
  const grid = el("dl");
  for (const descriptor of batchState.fields[row.candidate.type] || []) {
    const value = row.candidate[descriptor.name];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) continue;
    if (value === owner && row.id !== owner) continue;
    const shown = el("dd");
    if (descriptor.control === "alias_list") {
      shown.append(el("span", value.map((alias) => alias.value + (alias.kind && alias.kind !== "other"
        ? " (" + valueLabel(alias.kind) + ")" : "")).join(", ")));
    } else if (descriptor.targets) {
      const byId = new Map(batchState.review.candidates.map((other) => [other.id, other]));
      shown.append(el("span", (Array.isArray(value) ? value : [value])
        .map((id) => (byId.has(id) ? rowLabel(byId.get(id)) : String(id))).join("; ")));
    } else if (Array.isArray(value)) {
      shown.append(el("span", value.join(", ")));
    } else {
      shown.append(textOrLink(String(value)));
    }
    grid.append(el("dt", fieldLabel(descriptor.name, descriptor.name.split("_").join(" "))), shown);
  }
  if (showSource && row.source) {
    const shown = el("dd");
    shown.append(textOrLink(row.source));
    grid.append(el("dt", fieldLabel("source")), shown);
  }
  node.append(grid);
  return node;
}

function labelled(node, label) {
  node.setAttribute("aria-label", label);
  return node;
}

// `baseline` is the digest the kept acceptances were made against: the one on screen, or the one
// this page's own withdrawal produced. Any other digest means another client changed the batch.
async function showBatch(batchId, message, baseline) {
  rememberPage("batch", [batchId]);
  if (batchState.id !== batchId) {
    batchState.accepted = new Set();
    batchState.editing = null;
    batchState.form = null;
  }
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
    batchState.message = (batchState.message ? batchState.message + " " : "") + t("batch.changed");
  }
  batchState.review = doc.review;
  batchState.lines = doc.lines;
  batchState.fields = doc.fields || {};
  batchState.aliasFields = doc.alias_fields || [];
  const pending = new Set(doc.review.candidates.filter((row) => row.status === "pending").map((row) => row.id));
  for (const id of batchState.accepted) if (!pending.has(id)) batchState.accepted.delete(id);
  // A row another client committed or withdrew is no longer editable, and the use case would
  // refuse the edit; the form closes rather than inviting one.
  if (batchState.editing !== null && !pending.has(batchState.editing)) {
    batchState.editing = null;
    batchState.form = null;
  }
  renderBatch();
}

// Redrawn from the batch already held, so switching language keeps acceptances and an open form.
function renderBatch() {
  current = renderBatch;
  const review = batchState.review;
  const counts = {};
  for (const row of review.candidates) counts[row.status] = (counts[row.status] || 0) + 1;
  const header = el("dl");
  header.append(el("dt", t("batch.batch")), el("dd", review.batch_id));
  for (const [status, count] of Object.entries(counts)) header.append(el("dt", valueLabel(status)), el("dd", count));
  // One source for the whole batch is shown once here; rows from several sources show their own.
  const sources = new Set(review.candidates.map((row) => row.source).filter(Boolean));
  if (sources.size === 1) {
    const shown = el("dd");
    shown.append(textOrLink(Array.from(sources)[0]));
    header.append(el("dt", t("source.source")), shown);
  }
  const boxes = [];
  const cells = (row, owner) => {
    let pick = "";
    if (row.status === "pending") {
      pick = el("input");
      pick.type = "checkbox";
      pick.setAttribute("aria-label", t("batch.select.aria", { ordinal: row.ordinal }));
      boxes.push([pick, row.id]);
    }
    const status = el("span");
    status.append(el("span", valueLabel(row.status), "badge"));
    if (batchState.accepted.has(row.id)) status.append(" ", el("span", t("batch.accepted_badge"), "badge accepted"));
    const verbatim = el("details");
    verbatim.append(el("summary", t("batch.candidate")), el("pre", JSON.stringify(row.candidate, null, 2)));
    // Only a pending row opens a form or takes a decision: the use cases refuse an amendment or a
    // withdrawal of any other row, and the page does not offer what would be refused.
    const tools = el("div", null, "row-actions");
    if (row.status === "pending") {
      tools.append(button(t(batchState.editing === row.id ? "common.close" : "common.edit"), () => toggleEdit(row.id)));
      const ordinal = { ordinal: row.ordinal };
      if (!batchState.accepted.has(row.id)) {
        const accept = button(t("batch.accept"), () => acceptSelected([row.id]));
        tools.append(labelled(accept, t("batch.accept.aria", ordinal)));
      }
      const reject = button(t("batch.reject"), () => withdrawSelected([row.id]));
      tools.append(labelled(reject, t("batch.reject.aria", ordinal)));
    }
    return [pick, "#" + row.ordinal, status, proposal(row, owner, sources.size > 1), identityState(row),
      verbatim, tools];
  };
  const people = new Set(review.candidates.filter((row) => row.candidate.type === "person").map((row) => row.id));
  const groups = new Map();
  for (const row of review.candidates) {
    const owner = personOf(row, people);
    if (!groups.has(owner)) groups.set(owner, []);
    groups.get(owner).push(row);
  }
  // Rows about no staged person come last, after every person's group.
  const ordered = Array.from(groups).sort(([a], [b]) => (a === null) - (b === null));
  const editing = review.candidates.find((row) => row.id === batchState.editing && row.status === "pending");
  const sections = ordered.map(([owner, rows]) => {
    const head = owner === null ? null : rows.find((row) => row.id === owner);
    const title = head ? (head.candidate.name || rowLabel(head)) : t("batch.no_person");
    const section = labelled(el("section", null, "group"), title);
    const heading = el("h3", title);
    if (head) heading.append(" ", el("span", identityState(head), "badge"));
    section.append(heading);
    const possible = (head && head.possible_matches) || [];
    if (possible.length) {
      section.append(el("p", t("batch.possible", {
        names: possible.map((match) => match.canonical_name).join(", "),
        more: head.possible_matches_truncated ? t("batch.possible_more") : "",
      }), "notice"));
    }
    const open = rows.filter((row) => row.status === "pending" && !batchState.accepted.has(row.id))
      .map((row) => row.id);
    if (open.length) {
      section.append(button(t("batch.accept_for", { count: open.length, title }), () => acceptSelected(open)));
    }
    const headings = ["", "#", ...["status", "proposal", "identity", "staged"].map((c) => t("batch.column." + c)), ""];
    const grid = table(headings, rows.map((row) => cells(row, owner)));
    grid.className = "review";
    section.append(grid);
    if (editing && rows.includes(editing)) section.append(currentForm(editing));
    return section;
  });
  const selected = () => boxes.filter(([box]) => box.checked).map(([, id]) => id);
  const confirmArea = el("div");
  const actions = el("div", null, "actions");
  actions.append(
    button(t("batch.accept_selected"), () => acceptSelected(selected())),
    button(t("batch.withdraw_selected"), () => withdrawSelected(selected())),
  );
  const commit = button(t("batch.commit_accepted"), () => confirmCommit(confirmArea));
  commit.disabled = batchState.accepted.size === 0;
  batchState.commit = commit;
  actions.append(commit, el("span", t("batch.accepted_count", { count: batchState.accepted.size }), "notice"));
  const nodes = [header, el("p", warning("review"), "warning")];
  if (batchState.message) nodes.push(el("p", batchState.message, "notice"));
  nodes.push(actions, confirmArea, ...sections, button(t("sources.back"), () => showSources(null, [])));
  show(t("batch.title"), ...nodes);
}

// Opening a form reads nothing from the server: it is built from the batch already on screen and
// the field lists that came with it, so one row at a time is open. Closing one is the reviewer
// saying to drop what they typed, which is the one place the kept form is deliberately thrown away.
function toggleEdit(candidateId) {
  batchState.editing = batchState.editing === candidateId ? null : candidateId;
  if (batchState.editing === null) batchState.form = null;
  renderBatch();
}

// The open form holds edits nobody has saved yet. The batch re-renders for reasons that have
// nothing to do with them — an acceptance, which writes nothing at all, is the ordinary one — so
// the form's own nodes are kept and re-attached rather than rebuilt from the stored candidate,
// which would silently discard every unsaved value. It is rebuilt from the stored candidate only
// when its basis moved: a different row, or a batch whose content changed under it. A switch of
// language rebuilds it too, in the new language, carrying what it holds unsaved into the new form.
function currentForm(row) {
  const cached = batchState.form;
  const digest = batchState.review.batch_digest;
  if (cached && cached.id === row.id && cached.digest === digest) {
    if (cached.lang === lang) {
      // Whatever disabled these was an action that has since finished; a re-render is never busy.
      for (const control of cached.node.querySelectorAll(_CONTROLS)) control.disabled = false;
      return cached.node;
    }
    return keepForm(row, editForm(row, unsaved(cached.controls)));
  }
  return keepForm(row, editForm(row));
}

function keepForm(row, form) {
  batchState.form = { id: row.id, digest: batchState.review.batch_digest, lang, node: form.node,
    controls: form.controls };
  return form.node;
}

// What an open form would send now: each moved control's value, by field.
function unsaved(controls) {
  const draft = {};
  for (const [name, control] of controls) {
    const value = control.read();
    if (value !== undefined) draft[name] = value;
  }
  return draft;
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

// `<input type="text">` sanitises its value by removing CR and LF, so a staged multiline summary,
// fact value, observation, or evidence note would come back flattened — and, compared against the
// unflattened staged string, read as an edit nobody made. A textarea keeps the text exactly.
const LF = String.fromCharCode(10);
const CR = String.fromCharCode(13);

// `draft`, when given, is an unsaved value the control opens on instead of the stored one; it is still
// compared against the stored one, so rebuilding a form never turns an untouched field into an edit.
function textControl(descriptor, staged, draft) {
  const seed = seedText(staged);
  const opening = draft === undefined ? seed : seedText(draft);
  // A long value gets a textarea too, so it can be read whole while it is edited.
  const long = (text) => text.includes(LF) || text.includes(CR) || text.length > 60;
  const multiline = descriptor.control === "text" && (long(seed) || long(opening));
  const input = el(multiline ? "textarea" : "input");
  if (multiline) {
    input.rows = 4;
  } else {
    input.type = descriptor.control === "date" ? "date" : descriptor.control === "number" ? "number" : "text";
    if (descriptor.control === "number") {
      input.min = descriptor.min;
      input.max = descriptor.max;
      input.step = descriptor.step;
    }
  }
  input.value = seed;
  input.setAttribute("aria-label", fieldAria(descriptor.name));
  // Compared against what the control actually holds, not against what it was handed: a value a
  // browser normalised on the way in must not be patched back over a field nobody touched.
  const shown = input.value;
  input.value = opening;
  const numeric = descriptor.control === "number";
  return { node: input, read: () => (input.value === shown ? undefined : submitted(input.value, numeric)) };
}

function optionList(select, options, seed) {
  const blank = el("option", t("common.unset"));
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

function selectControl(descriptor, staged, options, draft) {
  const select = el("select");
  const seed = seedText(staged);
  optionList(select, options, seed);
  select.value = draft === undefined ? seed : seedText(draft);
  select.setAttribute("aria-label", fieldAria(descriptor.name));
  return { node: select, read: () => {
    const value = select.value === seed ? undefined : submitted(select.value, false);
    return descriptor.control === "boolean" && typeof value === "string" ? value === "true" : value;
  } };

}

// An enum field's options, each sent as its stored value and shown as this language labels it.
function enumOptions(descriptor) {
  return descriptor.options.map((value) => ({ value, label: valueLabel(value) }));
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

function multiControl(descriptor, staged, draft) {
  const select = el("select");
  select.multiple = true;
  const chosen = Array.isArray(staged) ? staged : [];
  const opening = Array.isArray(draft) ? draft : chosen;
  const options = referenceOptionsFor(descriptor);
  select.size = Math.min(Math.max(options.length, 2), 8);
  for (const option of options) {
    const node = el("option", option.label);
    node.value = option.value;
    node.selected = opening.includes(option.value);
    select.append(node);
  }
  select.setAttribute("aria-label", fieldAria(descriptor.name));
  const same = (values) => values.length === chosen.length && values.every((value) => chosen.includes(value));
  return {
    node: select,
    read: () => {
      const values = Array.from(select.selectedOptions).map((option) => option.value);
      return same(values) ? undefined : values;
    },
  };
}

// POSIX single-quoting, built from character codes so no backslash has to survive this file.
// Every part of the command below is format-opaque — a batch id, a candidate id, and a restored
// evidence id may carry anything non-blank — and JSON pasted into a shell unquoted loses the
// quotes `pctx import amend --patch` requires, so the advertised command would simply fail.
const SQ = String.fromCharCode(39);
const SQ_ESCAPED = SQ + String.fromCharCode(92) + SQ + SQ;

function shellQuote(value) {
  return SQ + String(value).split(SQ).join(SQ_ESCAPED) + SQ;
}

// `evidence_ids` names durable records outside this batch, so there is nothing to pick from here;
// the stored ids are shown with the command that changes them.
function readonlyControl(descriptor, staged, row) {
  const values = Array.isArray(staged) ? staged : [];
  const node = el("div");
  node.append(el("p", values.length ? values.join(", ") : t("common.none")));
  node.append(el("p", t("batch.change_with", { command: "pctx import amend " + shellQuote(batchState.id) + " "
    + shellQuote(row.id) + " --patch "
    + shellQuote(JSON.stringify({ [descriptor.name]: values })) }), "notice"));
  return { node, read: () => undefined };
}

function aliasControl(descriptor, staged, draft) {
  const stagedRows = Array.isArray(staged) ? staged : [];
  const container = el("div");
  const rows = [];

  const addRow = (base) => {
    const entry = { base, controls: [], node: el("div", null, "alias") };
    for (const field of batchState.aliasFields) {
      const control = field.control === "select"
        ? selectControl(field, base[field.name], enumOptions(field))
        : textControl(field, base[field.name]);
      entry.controls.push([field.name, control]);
      entry.node.append(el("span", fieldLabel(field.name), "notice"), control.node);
    }
    entry.node.append(button(t("common.remove"), () => {
      entry.removed = true;
      container.removeChild(entry.node);
    }));
    rows.push(entry);
    container.append(entry.node);
  };

  // Opened on a draft, its rows are the draft's: each re-emits itself untouched, and the list as a
  // whole is still compared against what was staged.
  for (const alias of Array.isArray(draft) ? draft : stagedRows) addRow(alias);
  const adder = button(t("alias.add"), () => addRow({}));
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

function buildControl(descriptor, row, draft) {
  const staged = row.candidate[descriptor.name];
  const opening = descriptor.name in draft ? draft[descriptor.name] : undefined;
  if (descriptor.control === "alias_list") return aliasControl(descriptor, staged, opening);
  if (descriptor.control === "readonly") return readonlyControl(descriptor, staged, row);
  if (descriptor.control === "candidate_multiselect") return multiControl(descriptor, staged, opening);
  if (descriptor.control === "candidate_select") {
    return selectControl(descriptor, staged, referenceOptionsFor(descriptor), opening);
  }
  if (descriptor.control === "boolean") {
    return selectControl(descriptor, staged, [
      { value: "true", label: t("common.yes") }, { value: "false", label: t("common.no") },
    ], opening);
  }
  if (descriptor.control === "select") {
    return selectControl(descriptor, staged, enumOptions(descriptor), opening);
  }
  return textControl(descriptor, staged, opening);
}

// Whether this person row owes or holds an identity decision. `match_candidates` is projected
// only while the row is ambiguous, so a row the reviewer already resolved has no list — and
// without a control of its own it could not be corrected from the page at all, though
// `AmendStagedCandidate` accepts both a different choice and a clearing one.
function hasMatchDecision(row) {
  return Boolean(row.match_candidates) || Boolean(row.candidate.matched_person_id);
}

// The identity picker, which is a control of the form like any other rather than an action beside
// it: a second button would post only its own field, and a reviewer who corrected a name and then
// chose a person would have the rename silently rebuilt away by the reload. One Save sends both.
//
// Choosing, changing, or clearing is one ordinary `matched_person_id` patch, which the use case
// accepts only when the matcher itself produced that person; clearing sends null, which re-runs
// the matcher and puts an ambiguous row back to ambiguous with its full list.
function matchPicker(row, draft) {
  const matches = row.match_candidates || [];
  const resolved = seedText(row.candidate.matched_person_id);
  const select = el("select");
  const unresolved = el("option", t("match.unresolved"));
  unresolved.value = "";
  select.append(unresolved);
  // The row's own choice stays on the list even once the projection no longer names it, so the
  // control opens on what is recorded rather than silently on "unresolved".
  if (resolved !== "" && !matches.some((match) => match.id === resolved)) {
    const kept = el("option", t("match.current", { id: resolved }));
    kept.value = resolved;
    select.append(kept);
  }
  for (const match of matches) {
    const option = el("option", match.canonical_name + (match.name_truncated ? t("match.shortened") : "")
      + " - " + match.id);
    option.value = match.id;
    select.append(option);
  }
  select.value = resolved;
  select.setAttribute("aria-label", t("match.aria"));
  const typed = el("input");
  typed.type = "text";
  typed.setAttribute("aria-label", t("match.person_id"));
  typed.placeholder = t("match.person_id");
  const nodes = [el("p", t(matches.length ? "match.several" : "match.resolved")), select];
  // A pasted id is the only way to reach a person the projection does not list, which is either
  // because there are more than it shows or because the row is no longer ambiguous.
  if (row.match_candidates_truncated || !matches.length) {
    nodes.push(
      el("p", t("match.find"), "notice"),
      typed,
    );
  }
  // An unsaved choice reopens as the reviewer left it: a listed person selected, anything else pasted.
  if (draft !== undefined) {
    const listed = draft === null || draft === resolved || matches.some((match) => match.id === draft);
    select.value = listed ? seedText(draft) : resolved;
    if (!listed) typed.value = draft;
  }
  const node = el("div");
  node.append(...nodes);
  // Present-and-null is how a person row records "matched nothing", so the comparison normalises
  // both sides. Only an actual change is sent: naming the field at all reopens identity in the
  // use case, which would put a decision the reviewer already made back to ambiguous.
  const current = row.candidate.matched_person_id === undefined ? null : row.candidate.matched_person_id;
  return {
    node,
    read: () => {
      // Verbatim, never trimmed: a person id is format-opaque and the matcher compares it
      // exactly, so a restored id carrying edge whitespace has to travel as it was pasted. Only
      // whether the box was used at all is decided by trimming.
      const pasted = typed.value;
      const chosen = pasted.trim() === "" ? (select.value || null) : pasted;
      return chosen === current ? undefined : chosen;
    },
  };
}

// `draft` holds unsaved values to reopen on, by field, when the form is rebuilt in another language.
function editForm(row, draft) {
  const opening = draft || {};
  const descriptors = (batchState.fields || {})[row.candidate.type] || [];
  const controls = [];
  const grid = el("dl");
  const panel = el("div", null, "edit");
  panel.append(el("h3", t("batch.editing", { ordinal: row.ordinal, type: valueLabel(row.candidate.type) })));
  if (hasMatchDecision(row)) {
    const picker = matchPicker(row, "matched_person_id" in opening ? opening.matched_person_id : undefined);
    picker.slot = el("div");
    controls.push(["matched_person_id", picker]);
    const holder = el("div");
    holder.append(picker.node, picker.slot);
    panel.append(holder);
  }
  for (const descriptor of descriptors) {
    const control = buildControl(descriptor, row, opening);
    control.slot = el("div");
    controls.push([descriptor.name, control]);
    const value = el("dd");
    value.append(control.node, control.slot);
    grid.append(el("dt", fieldLabel(descriptor.name)), value);
  }
  const rowErrors = el("div");
  panel.append(grid, rowErrors);
  const actions = el("div", null, "actions");
  actions.append(
    button(t("common.save"), () => saveEdit(row, controls, rowErrors)),
    button(t("common.cancel"), () => toggleEdit(row.id)),
  );
  panel.append(actions);
  return { node: panel, controls };
}

function saveEdit(row, controls, rowErrors) {
  const patch = {};
  for (const [name, control] of controls) {
    const value = control.read();
    if (value !== undefined) patch[name] = value;
  }
  if (!Object.keys(patch).length) {
    rowErrors.replaceChildren(el("p", t("batch.nothing_changed"), "notice"));
    return;
  }
  return amendRow(row, patch, controls, rowErrors, t("batch.amended", { ordinal: row.ordinal }));
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
    // A stale digest is not something the reviewer can correct in the open form: the batch has
    // moved, and every later Save from this form would repeat the digest that was already
    // refused. Reload instead, as withdraw and commit do, so the concurrent change is on screen.
    if (error.message === "batch_changed") {
      return showBatch(batchId, t("refused.code", { code: "batch_changed" }));
    }
    showRefusal(error, controls, rowErrors);
    return reenable();
  }
  batchState.busy = false;
  batchState.editing = null;
  batchState.form = null;
  // An acceptance is a decision about content that was on screen, and this amendment changed
  // content — either the accepted row's own, or, for a person row, the identity an accepted
  // dependent resolves through. Carrying the acceptances across would let the next commit write
  // something nobody accepted in its amended state, and the digest this edit produced would not
  // refuse it. They are dropped whole rather than guessed at row by row.
  const dropped = batchState.accepted.size;
  batchState.accepted = new Set();
  if (dropped) message += " " + t("batch.acceptances_dropped", { count: dropped });
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
  const rest = [el("p", t("refused", { reason: error.message }), "error")];
  for (const entry of error.fields || []) {
    if (!shown.has(entry.field)) rest.push(el("p", fieldLabel(entry.field) + ": " + entry.message, "error"));
  }
  rowErrors.replaceChildren(...rest);
}

function acceptSelected(ids) {
  for (const id of ids) batchState.accepted.add(id);
  return showBatch(batchState.id, ids.length ? t("batch.accepted", { count: ids.length }) : null);
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
    message = t("batch.withdrew", { count: result.withdrawn });
    baseline = result.batch_digest;
  } catch (error) { message = t("refused", { reason: error.message }); } finally { batchState.busy = false; }
  if (at === navigation) return showBatch(batchId, message, baseline);
}

// One explicit click, on a confirmation naming exactly the accepted rows. The result may commit
// fewer: an ambiguous person, or a row that resolves through one, is reported unresolved.
function confirmCommit(area) {
  const count = batchState.accepted.size;
  const chosen = batchState.review.candidates.filter((row) => batchState.accepted.has(row.id));
  const panel = el("div", null, "confirm");
  panel.append(
    el("p", t("batch.commit_confirm", { count })),
    list(chosen.map(rowLabel)),
    button(t("batch.commit_confirm_button", { count }), () => commitAccepted()),
    " ",
    button(t("common.cancel"), () => area.replaceChildren()),
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
    message = t("batch.committed", {
      committed: result.committed_ids.length,
      unresolved: result.unresolved_ids.length,
      skipped: result.skipped_ids.length,
    });
  } catch (error) { message = t("refused", { reason: error.message }); } finally { batchState.busy = false; }
  if (at === navigation) return showBatch(batchId, message);
}

async function done() {
  // Stopping is a navigation too: no pending view or batch response may repaint after it.
  const at = navigate();
  viewerClosed = true;
  try { await api("/api/done", { method: "POST" }); } catch (error) {
    viewerClosed = false;
    return fail(error, at);
  }
  if (at !== navigation) return;
  current = () => view.replaceChildren(el("p", t("done.stopped")));
  current();
}

window.addEventListener("popstate", (event) => {
  const state = event.state;
  if (viewerClosed || !state || state.pctx !== true || !Array.isArray(state.args)) return;
  const open = { people: showPeople, person: showPerson, sources: showSources,
    source: showSource, batch: showBatch }[state.page];
  if (typeof open !== "function") return;
  Object.assign(peopleFilter, state.filters);
  restoringHistory = true;
  try { open(...state.args); } finally { restoringHistory = false; }
});

document.getElementById("nav-people").addEventListener("click", () => showPeople(null, []));
document.getElementById("nav-sources").addEventListener("click", () => showSources(null, []));
document.getElementById("nav-done").addEventListener("click", done);
document.getElementById("nav-lang").addEventListener("click", switchLanguage);
applyLanguage();
showPeople(null, []);
</script>
</body>
</html>
"""


def render_page(nonce: str, *, review_warning: str, sources_warning: str, language: str = "en") -> str:
    """Return the document with this response's nonce, its opening language, and its text filled in.

    The warnings are the caller's English wording; their translations come from `i18n.WARNINGS`.
    """
    if language not in MESSAGES:
        raise ValueError(f"unsupported language {language!r}")
    catalogue = {
        "default": language,
        "languages": list(MESSAGES),
        "messages": MESSAGES,
        "fields": FIELD_LABELS,
        "values": VALUE_LABELS,
        "warnings": {"en": {"review": review_warning, "sources": sources_warning}, **WARNINGS},
    }
    # Inside a script, `</script>` or `<!--` would end or bend it; escaping every `<`, `>`, and `&`
    # makes the JSON inert there whatever the warnings say, and JavaScript reads it unchanged.
    text = json.dumps(catalogue, ensure_ascii=False)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _TEMPLATE.replace(_I18N, text).replace(_LANG, language).replace(_NONCE, nonce)
