"""Frontmatter contract shared by every bundled skill.

Each skill has its own behavioural suite, and each of those parses frontmatter with the same
naive ``key: value`` split — which is exactly why neither the suites nor the marketplace
validator noticed that two descriptions were not valid YAML at all. Skill frontmatter is
YAML, so a plain (unquoted) scalar ends at the first ``": "`` it contains: a description
reading ``... not for every mention of a person: identifying someone ...`` makes a parser
report ``mapping values are not allowed in this context``, and a client that reads the
metadata properly can reject or skip the skill. Agent Skills also cap ``description`` at
1024 characters, and an over-long one can be dropped the same way.

Both failures are invisible to a per-line split, so they are pinned here once for every
skill rather than in each behavioural suite. The check is deliberately hand-rolled: PyYAML
reaches this repository only as a transitive dependency of the optional ``semantic`` extra,
and a contract this load-bearing must not silently stop running under a plain ``uv sync``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"

# Agent Skills reject a description longer than this.
DESCRIPTION_LIMIT = 1024

SKILL_PATHS = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
SKILL_NAMES = [path.parent.name for path in SKILL_PATHS]


def _frontmatter_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must open with YAML frontmatter"
    _, frontmatter, _ = text.split("---\n", 2)
    return [line for line in frontmatter.splitlines() if line.strip()]


def _scalar(value: str) -> tuple[str, bool]:
    """Return a frontmatter value and whether it was written as a quoted YAML scalar."""
    value = value.strip()
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1], True
    return value, False


def test_the_repository_ships_the_skills_under_test() -> None:
    # A glob that silently matched nothing would make every test below vacuously pass.
    assert SKILL_PATHS
    assert "transcript-review" in SKILL_NAMES
    assert not (REPOSITORY_ROOT / ".claude-plugin" / "skills").exists()


@pytest.mark.parametrize("path", SKILL_PATHS, ids=SKILL_NAMES)
class TestSkillFrontmatterIsValidYaml:
    def test_every_line_is_a_key_value_pair(self, path: Path) -> None:
        for line in _frontmatter_lines(path):
            key, separator, _ = line.partition(":")
            assert separator, f"{path}: malformed frontmatter line {line!r}"
            assert key.strip(), f"{path}: empty frontmatter key in {line!r}"

    def test_an_unquoted_value_never_contains_a_colon_followed_by_a_space(self, path: Path) -> None:
        # ``": "`` terminates a plain YAML scalar, so the rest of the line parses as a
        # nested mapping and the document fails to load. Quote the value instead.
        for line in _frontmatter_lines(path):
            key, _separator, raw = line.partition(":")
            value, quoted = _scalar(raw)
            if quoted:
                continue
            assert ": " not in value, (
                f"{path}: unquoted {key.strip()} contains ': ', which is invalid YAML; quote the value"
            )

    def test_the_name_matches_the_directory(self, path: Path) -> None:
        for line in _frontmatter_lines(path):
            key, _separator, raw = line.partition(":")
            if key.strip() == "name":
                assert _scalar(raw)[0] == path.parent.name
                return
        pytest.fail(f"{path}: frontmatter declares no name")

    def test_the_description_is_present_and_within_the_limit(self, path: Path) -> None:
        for line in _frontmatter_lines(path):
            key, _separator, raw = line.partition(":")
            if key.strip() == "description":
                description = _scalar(raw)[0]
                assert description, f"{path}: empty description"
                assert len(description) <= DESCRIPTION_LIMIT, (
                    f"{path}: description is {len(description)} characters, over the {DESCRIPTION_LIMIT} limit"
                )
                return
        pytest.fail(f"{path}: frontmatter declares no description")
