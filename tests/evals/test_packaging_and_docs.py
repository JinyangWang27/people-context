"""The harness must stay out of shipped artifacts, and the docs must match the results."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from evals.harness import HARNESS_VERSION, REPORT_FORMAT, REPORT_VERSION

ROOT = Path(__file__).parents[2]
EVALS_DOC = ROOT / "docs/evals.md"
GALLERY = ROOT / "docs/use-cases"
RESULTS = ROOT / "evals/results"

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _recorded_reports() -> list[Path]:
    return sorted(RESULTS.glob("*.json"))


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build both distributions once for the exclusion checks."""
    uv = shutil.which("uv")
    assert uv is not None
    out = tmp_path_factory.mktemp("dist")
    built = subprocess.run(
        [uv, "build", "--out-dir", str(out)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return out


def test_the_source_distribution_ships_no_evaluation_assets(distributions: Path) -> None:
    """Fixtures, prompts, and a process-starting runner have no place in a release."""
    sdist = next(distributions.glob("people_context-*.tar.gz"))

    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    assert names, "the source distribution must not be empty"
    inside = [name.split("/", 1)[1] for name in names if "/" in name]
    leaked = [name for name in inside if name.startswith(("evals/", "tests/evals/"))]
    assert not leaked, f"evaluation assets leaked into the sdist: {leaked}"
    assert any(name.startswith("src/people_context/") for name in inside)


def test_the_wheel_ships_no_evaluation_assets(distributions: Path) -> None:
    wheel = next(distributions.glob("people_context-*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert names
    assert not [name for name in names if name.startswith("evals/")]
    assert any(name.startswith("people_context/") for name in names)


def test_the_exclusion_is_declared_rather_than_incidental() -> None:
    """Regression: without the sdist exclusion, hatchling would include every tracked file."""
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"] == ["/evals", "/tests/evals"]
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/people_context"]


def test_no_shipped_module_imports_the_harness() -> None:
    """The dependency runs one way: the harness may use the package, never the reverse."""
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "src/people_context").rglob("*.py")
        if re.search(r"^\s*(from|import)\s+evals\b", _read(path), re.MULTILINE)
    ]

    assert not offenders


def test_every_recorded_report_is_a_current_report_document() -> None:
    reports = _recorded_reports()

    assert reports, "at least one recorded run must be published"
    for path in reports:
        report = json.loads(_read(path))
        assert report["format"] == REPORT_FORMAT, path.name
        assert report["version"] == REPORT_VERSION, path.name
        assert report["harness_version"] == HARNESS_VERSION, path.name


def test_every_recorded_report_matches_the_current_rubrics() -> None:
    """A recorded report whose criteria have drifted from the suite is stale evidence."""
    suite = json.loads(_read(ROOT / "evals/suite/suite.json"))
    expected = {task["id"]: [item["id"] for item in task["rubric"]] for task in suite["tasks"]}

    for path in _recorded_reports():
        for run in json.loads(_read(path))["runs"]:
            recorded = [criterion["id"] for criterion in run["criteria"]]
            assert recorded == expected[run["task_id"]], f"{path.name}: {run['task_id']} is stale"


def test_the_documented_harness_version_matches_the_code() -> None:
    assert f"Harness version: **{HARNESS_VERSION}**" in _read(EVALS_DOC)


def test_every_recorded_report_is_published_and_every_published_number_is_recorded() -> None:
    """A results file nobody documents, or a documented number nobody can check, is not evidence."""
    document = _read(EVALS_DOC)

    for path in _recorded_reports():
        relative = f"../evals/results/{path.name}"
        assert relative in document, f"{path.name} is not referenced from docs/evals.md"
        report = json.loads(_read(path))
        for total in report["totals"]:
            row = (
                f"| `{total['condition']}` | {total['tasks']} | {total['earned']} "
                f"| {total['possible']} | {total['percent']} |"
            )
            assert row in document, f"docs/evals.md does not publish the recorded row: {row}"


def test_the_dry_run_is_documented_as_plumbing_rather_than_a_model_result() -> None:
    """Regression: scripted answers must never read as a measurement of a model."""
    document = _read(EVALS_DOC)

    assert "**This is not a measurement of any model.**" in document
    assert "**None recorded yet.**" in document
    for report in _recorded_reports():
        assert json.loads(_read(report))["runner"]["kind"] == "stub", (
            "a model-backed report is published; update the 'None recorded yet' section"
        )


def test_the_docs_state_the_key_handling_the_suite_actually_enforces() -> None:
    document = _read(EVALS_DOC)

    assert "read only from the process environment" in document
    assert "PEOPLE_CONTEXT" in document


def test_the_gallery_holds_between_three_and_five_recipes() -> None:
    recipes = sorted(path.name for path in GALLERY.glob("*.md") if path.name != "README.md")

    assert 3 <= len(recipes) <= 5, recipes


def test_the_gallery_index_links_every_recipe() -> None:
    index = _read(GALLERY / "README.md")

    for recipe in GALLERY.glob("*.md"):
        if recipe.name == "README.md":
            continue
        assert f"]({recipe.name})" in index, f"{recipe.name} is missing from the gallery index"


@pytest.mark.parametrize(
    "relative",
    ["docs/evals.md", *(f"docs/use-cases/{path.name}" for path in sorted(GALLERY.glob("*.md")))],
)
def test_relative_links_resolve(relative: str) -> None:
    source = ROOT / relative
    targets = _MARKDOWN_LINK.findall(_read(source))

    assert targets, f"{relative} links nothing"
    for target in targets:
        resolved = (source.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link in {relative}: {target}"


def test_the_readme_documentation_table_lists_the_new_pages() -> None:
    readme = _read(ROOT / "README.md")

    assert "[docs/evals.md](docs/evals.md)" in readme
    assert "[docs/use-cases](docs/use-cases/README.md)" in readme
