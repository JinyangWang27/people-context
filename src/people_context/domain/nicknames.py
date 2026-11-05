"""Common English given-name short forms, used only to *suggest* an existing identity.

A shared short form is weak evidence: many unrelated people are called Kate. Nothing here may decide
an identity — it only widens which existing people a reviewer is shown as possibly the same person.

shortcut: a fixed English-only table, upgrade to a locale-aware source if suggestions prove too narrow.
"""

from __future__ import annotations

from typing import Final

from people_context.domain.shared import normalize_name

#: Each group lists forms of one given name that people use interchangeably.
_GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("alexander", "alexandra", "alex", "alec", "sasha", "lex"),
    ("andrew", "andy", "drew"),
    ("anthony", "tony"),
    ("benjamin", "ben", "benny"),
    ("catherine", "katherine", "kathryn", "katharine", "kate", "katie", "kathy", "cathy", "kat", "kitty"),
    ("charles", "charlie", "chuck"),
    ("christopher", "chris", "topher"),
    ("christina", "christine", "chris", "tina", "chrissy"),
    ("daniel", "dan", "danny"),
    ("david", "dave", "davey"),
    ("deborah", "debra", "deb", "debbie"),
    ("edward", "ed", "eddie", "ned", "ted"),
    ("elizabeth", "liz", "lizzie", "beth", "betty", "eliza", "libby"),
    ("emily", "em", "emmy"),
    ("frederick", "fred", "freddie"),
    ("gregory", "greg"),
    ("jacob", "jake"),
    ("james", "jim", "jimmy", "jamie"),
    ("jennifer", "jen", "jenny"),
    ("jessica", "jess", "jessie"),
    ("john", "jack", "johnny"),
    ("jonathan", "jon", "jonny"),
    ("joseph", "joe", "joey"),
    ("joshua", "josh"),
    ("margaret", "maggie", "meg", "peggy", "marge"),
    ("matthew", "matt"),
    ("michael", "mike", "mikey", "mick"),
    ("nicholas", "nick", "nicky"),
    ("patricia", "pat", "patty", "trish"),
    ("patrick", "pat", "paddy"),
    ("rebecca", "becca", "becky"),
    ("richard", "rich", "rick", "dick"),
    ("robert", "rob", "bob", "bobby", "robbie", "bert"),
    ("samantha", "sam", "sammy"),
    ("samuel", "sam", "sammy"),
    ("stephen", "steven", "steve"),
    ("susan", "sue", "suzy"),
    ("thomas", "tom", "tommy"),
    ("timothy", "tim", "timmy"),
    ("victoria", "vicky", "tori"),
    ("william", "will", "bill", "billy", "liam"),
)

_VARIANTS: Final[dict[str, frozenset[str]]] = {}
for _group in _GROUPS:
    for _form in _group:
        _VARIANTS[_form] = _VARIANTS.get(_form, frozenset()) | frozenset(_group)


def given_name_variants(given_name: str) -> frozenset[str]:
    """Return the normalized short and long forms of one given name, excluding the name itself."""
    normalized = normalize_name(given_name)
    return _VARIANTS.get(normalized, frozenset()) - {normalized}
