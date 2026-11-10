"""The `pctx browse` page in each language it offers, and its 中文 / EN toggle."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from people_context.adapters.runtime import build_runtime
from people_context.adapters.web.fields import ALIAS_FIELDS, CANDIDATE_FIELDS, RECORD_FIELDS
from people_context.adapters.web.i18n import FIELD_LABELS, MESSAGES, VALUE_LABELS, WARNINGS
from people_context.adapters.web.page import _TEMPLATE, render_page
from people_context.config import BROWSE_LANGUAGES
from people_context.domain.import_provenance import STAGED_CANDIDATE_TYPES
from people_context.domain.person import AliasKind
from tests.adapters.web.test_browse_app import _run_page

_PLACEHOLDER = re.compile(r"[{]([a-z_]+)[}]")
_OTHER_LANGUAGES = [code for code in BROWSE_LANGUAGES if code != "en"]

#: Keys the page builds from a prefix and a known suffix rather than naming whole.
_COMPOSED_KEYS = {
    *(f"people.sort.{value}" for value in ("name", "created", "updated")),
    *(f"people.column.{column}" for column in ("preferred_name", "name", "organization", "aliases", "summary")),
    *(f"sources.column.{column}" for column in ("kind", "label", "status", "batch")),
    *(f"batch.column.{column}" for column in ("status", "proposal", "identity", "staged")),
    *(
        f"adder.{noun}{suffix}"
        for noun in ("affiliation", "fact", "interaction")
        for suffix in ("", ".aria", ".missing")
    ),
    *(
        f"adder.{name}"
        for name in ("organization", "role", "from", "to", "predicate", "value", "summary", "channel", "on")
    ),
    "person.edit_summary",
    "person.edit_preferred_name",
    "person.edit_given_name",
    "person.edit_family_name",
    "person.preferred_name",
    "person.given_name",
    "person.family_name",
}


def test_every_offered_language_has_a_catalogue() -> None:
    assert tuple(MESSAGES) == BROWSE_LANGUAGES
    assert set(FIELD_LABELS) == set(VALUE_LABELS) == set(WARNINGS) == set(_OTHER_LANGUAGES)


@pytest.mark.parametrize("language", _OTHER_LANGUAGES)
def test_each_language_says_every_sentence_with_the_same_placeholders(language: str) -> None:
    assert set(MESSAGES[language]) == set(MESSAGES["en"])
    for key, english in MESSAGES["en"].items():
        assert set(_PLACEHOLDER.findall(MESSAGES[language][key])) == set(_PLACEHOLDER.findall(english)), key


def test_every_key_the_page_asks_for_is_in_the_catalogue() -> None:
    # A key ending in `.` or `_` is a prefix the page completes; those are `_COMPOSED_KEYS`.
    named = {key for key in re.findall(r'\bt[(]"([a-z_.]+)"', _TEMPLATE) if not key.endswith((".", "_"))}
    assert named, "the page names its text through t()"
    assert named - set(MESSAGES["en"]) == set()
    assert _COMPOSED_KEYS - set(MESSAGES["en"]) == set()
    # Refusal codes are looked up by `"refusal." + code`; each one has its sentence.
    assert {key for key in MESSAGES["en"] if key.startswith("refusal.")}


def test_every_key_in_the_catalogue_is_used() -> None:
    # Named anywhere as a literal, which includes a key chosen by a conditional inside `t(...)`.
    unused = {
        key
        for key in MESSAGES["en"]
        if f'"{key}"' not in _TEMPLATE and key not in _COMPOSED_KEYS and not key.startswith("refusal.")
    }
    assert unused == set()


@pytest.mark.parametrize("language", _OTHER_LANGUAGES)
def test_every_form_field_and_option_has_a_label(language: str) -> None:
    descriptors = [d for group in (CANDIDATE_FIELDS, RECORD_FIELDS) for ds in group.values() for d in ds]
    descriptors += ALIAS_FIELDS
    names = {d["name"] for d in descriptors} | {"source", "matched_person_id"}
    assert names - set(FIELD_LABELS[language]) == set()
    options = {option for d in descriptors for option in d.get("options", [])}
    shown = options | set(STAGED_CANDIDATE_TYPES) | {kind.value for kind in AliasKind}
    shown |= {"pending", "committed", "rejected", "ordinary"}
    assert shown - set(VALUE_LABELS[language]) == set()


def test_the_page_opens_in_the_configured_language_with_its_text_inert_in_the_script() -> None:
    page = render_page("n", review_warning="Careful </script><!-- & more", sources_warning="s", language="zh-CN")
    assert '<html lang="zh-CN">' in page
    assert '"default": "zh-CN"' in page
    start = page.index(">", page.index("<script")) + 1
    script = page[start : page.index("</script>", start)]
    # The warning is inside the script, escaped, and cannot close it.
    assert "Careful \\u003c/script\\u003e\\u003c!-- \\u0026 more" in script
    assert "</script>" not in script and "<!--" not in script
    with pytest.raises(ValueError, match="unsupported language"):
        render_page("n", review_warning="r", sources_warning="s", language="fr")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "people.db"
    build_runtime(path).close()
    return path


_CHINESE_AND_TOGGLE = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  const texts = () => view.all().map((n) => n.textContent || n.text).filter((t) => typeof t === "string" && t);
  const nav = (id) => document.getElementById(id);
  globalThis.personDoc = { person: { id: "P", canonical_name: "Jordan {label} Lee", aliases: [], emails: [] },
    disclosure: { context: "ordinary" }, reminders: [], traits: [], alias_kinds: ["nickname", "handle"],
    alias_details: [], record_fields: __RECORD_FIELDS__, interactions: [],
    affiliations: [{ affiliation: { id: "A1", role: "Eng", period: {} }, organization_name: "Acme" }],
    facts: [{ id: "F1", predicate: "role", value: "{label}", sensitivity: "personal", period: {} }] };
  const opening = { title: document.title, people: nav("nav-people").textContent, toggle: nav("nav-lang").textContent };
  await showPerson("P", null, []);
  const headings = view.all().filter((n) => n.tag === "h3").map((n) => n.textContent);
  // A recorded value holding a placeholder is placed as is, never expanded.
  labelled("删除 role: {label}").listeners.click();
  const prompt = texts().find((t) => t.startsWith("永久删除"));
  find("取消").listeners.click();
  labelled("编辑 role: {label}").listeners.click();
  const sensitivity = labelled("敏感级别");
  const options = sensitivity.children.map((o) => [o.value, o.textContent]);
  // The toggle switches to English and redraws the person in place.
  nav("nav-lang").listeners.click(); await tick();
  const english = { people: nav("nav-people").textContent, toggle: nav("nav-lang").textContent,
    headings: view.all().filter((n) => n.tag === "h3").map((n) => n.textContent),
    affiliation: texts().includes("Eng at Acme") };
  nav("nav-lang").listeners.click(); await tick();
  console.log(JSON.stringify({ opening, headings, prompt, options, english,
    back: nav("nav-people").textContent, affiliation: texts().includes("Acme · Eng"), posts: posts.length }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_opens_in_chinese_and_the_toggle_switches_in_place(db_file: Path, tmp_path: Path) -> None:
    scenario = _CHINESE_AND_TOGGLE.replace("__RECORD_FIELDS__", json.dumps(RECORD_FIELDS))
    outcome = _run_page(db_file, tmp_path, scenario, language="zh-CN")
    assert outcome["opening"] == {"title": "People Context", "people": "联系人", "toggle": "EN"}
    assert outcome["headings"] == ["人际关系", "所属机构", "事实", "互动记录", "提醒", "特质"]
    assert outcome["prompt"] == "永久删除 role: {label}？此操作无法撤销。"
    # Options are sent as their stored values and labelled in the language on screen.
    assert ["personal", "个人"] in outcome["options"] and ["sensitive", "敏感"] in outcome["options"]
    assert outcome["english"] == {
        "people": "People",
        "toggle": "中文",
        "headings": ["Relationships", "Affiliations", "Facts", "Interactions", "Reminders", "Traits"],
        "affiliation": True,
    }
    assert (outcome["back"], outcome["affiliation"]) == ("联系人", True)
    assert outcome["posts"] == 0


_SWITCH_WITH_A_FORM_OPEN = r"""
(async () => {
  const find = (label) => view.all().find((n) => n.tag === "button" && n.textContent === label);
  const labelled = (name) => view.all().find((n) => (n.attrs || {})["aria-label"] === name);
  setRow({ candidate: { type: "person", name: "Elena Marsh", summary: "Allotment", matched_person_id: null,
    match_disposition: "ambiguous", match_count: 2,
    aliases: [{ value: "Lena", kind: "nickname" }] },
    match_candidates: [{ id: "person-1", canonical_name: "Elena Marsh", name_truncated: false },
      { id: "person-2", canonical_name: "Elena Marsh", name_truncated: false }] });
  await showBatch("B");
  find("Edit").listeners.click();
  labelled("name").value = "Elena Marshe";
  labelled("Matching person").value = "person-2";
  document.getElementById("nav-lang").listeners.click(); await tick();
  const panel = view.all().find((n) => n.tag === "div" && n.className === "edit");
  const heading = panel.children[0].textContent;
  const terms = panel.all().filter((n) => n.tag === "dt").map((n) => n.textContent);
  const kept = { name: labelled("名称").value, summary: labelled("摘要").value,
    match: labelled("匹配的联系人").value };
  const kinds = labelled("类型").children.map((o) => [o.value, o.textContent]);
  find("保存").listeners.click();
  releases.forEach((r) => r()); await tick();
  console.log(JSON.stringify({ heading, terms, kept, kinds, patch: JSON.parse(posts[0].body).patch }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_switching_language_rebuilds_an_open_form_in_place_keeping_what_was_typed(
    db_file: Path, tmp_path: Path
) -> None:
    outcome = _run_page(db_file, tmp_path, _SWITCH_WITH_A_FORM_OPEN)
    assert outcome["heading"] == "正在编辑 #1（联系人）"
    assert "名称" in outcome["terms"] and "别名" in outcome["terms"]
    assert outcome["kept"] == {"name": "Elena Marshe", "summary": "Allotment", "match": "person-2"}
    assert ["nickname", "昵称"] in outcome["kinds"]
    # Only what the reviewer changed is sent: the untouched summary and alias list stay out.
    assert outcome["patch"] == {"name": "Elena Marshe", "matched_person_id": "person-2"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(("language", "label"), [("en", "Close"), ("zh-CN", "关闭")])
def test_close_button_names_and_ends_the_viewer_session(
    db_file: Path, tmp_path: Path, language: str, label: str
) -> None:
    scenario = r"""
    (async () => {
      await tick();
      const control = document.getElementById("nav-done");
      const label = control.textContent;
      const closing = control.listeners.click();
      releases.forEach((r) => r()); await closing;
      console.log(JSON.stringify({label, posts: posts.map((p) => p.path), text: view.children[0].textContent}));
    })();
    """
    assert _run_page(db_file, tmp_path, scenario, language=language) == {
        "label": label,
        "posts": ["/api/done"],
        "text": MESSAGES[language]["done.stopped"],
    }
