"""Safe onboarding and isolated fictional demo commands."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.context import SetCommunicationPhilosophyInput
from people_context.app.imports import ImportPipelineError
from people_context.app.people import (
    AliasInput,
    AmbiguousPersonError,
    RememberPersonInput,
    SelfAlreadyExistsError,
)
from people_context.app.records import RecordFactInput, RecordInteractionInput, SetAffiliationInput
from people_context.app.relationships import SetRelationshipInput
from people_context.cli.rendering import print_demo_instructions, print_import_review
from people_context.demo_seed import (
    DEMO_AFFILIATIONS,
    DEMO_FACTS,
    DEMO_INTERACTIONS,
    DEMO_PEOPLE,
    DEMO_RELATIONSHIPS,
)
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import normalize_name

_DEMO_FILENAME = "demo.db"


def cmd_init(runtime: ApplicationRuntime) -> int:
    """Run safe, additive onboarding through audited application use cases."""
    target, fresh, exit_code = _preflight_init(runtime)
    if exit_code != 0:
        return exit_code
    if not fresh:
        assert target is not None
        answer = input(f"Add onboarding data to existing self {target.canonical_name} ({target.id})? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            print("Aborted.")
            return 0
        name = target.canonical_name
    else:
        name = " ".join(input("Canonical name: ").split())
        if not name:
            print("Canonical name must not be blank.", file=sys.stderr)
            return 2

    aliases = _prompt_email_aliases()
    if aliases is None:
        return 2
    if not _preflight_init_aliases(runtime, target, aliases):
        return 1
    raw_vcard_path = input("vCard path (leave blank to skip): ").strip()
    vcard_path: Path | None = None
    if raw_vcard_path:
        vcard_path = _preflight_vcard_path(raw_vcard_path)
        if vcard_path is None:
            return 1

    try:
        remembered = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name=name,
                aliases=aliases,
                is_self=True,
                source="cli/init",
            )
        )
    except (AmbiguousPersonError, SelfAlreadyExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target = remembered.person
    print(f"Self identity {'created' if remembered.created else 'updated'}: {target.canonical_name} ({target.id}).")

    if vcard_path:
        import_exit = _run_init_vcard_import(runtime, vcard_path, target)
        if import_exit != 0:
            return import_exit

    philosophy = input("Communication philosophy (optional, one line): ").strip()
    if philosophy:
        if "\n" in philosophy or "\r" in philosophy:
            print("Communication philosophy must be one line.", file=sys.stderr)
            return 2
        runtime.use_cases.set_communication_philosophy.execute(
            SetCommunicationPhilosophyInput(text=philosophy, source="cli/init")
        )
        print("Communication philosophy recorded.")
    print("Onboarding complete.")
    return 0


def _preflight_init(runtime: ApplicationRuntime) -> tuple[Person | None, bool, int]:
    people = runtime.repo.list_people(include_deleted=True)
    if not people:
        return None, True, 0
    self_people = [person for person in people if person.deleted_at is None and person.is_self]
    if len(self_people) != 1:
        print("Cannot continue: non-empty state must contain exactly one active self person.", file=sys.stderr)
        return None, False, 1
    target = self_people[0]
    matches = runtime.repo.find_by_normalized_name(normalize_name(target.canonical_name))
    if len(matches) != 1 or matches[0].id != target.id:
        print("Cannot continue: the existing self identity is ambiguous.", file=sys.stderr)
        return None, False, 1
    return target, False, 0


def _prompt_email_aliases() -> list[AliasInput] | None:
    raw = input("Email handles (comma-separated, optional): ")
    aliases: list[AliasInput] = []
    seen: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        address = normalize_name(item)
        local, separator, domain = address.partition("@")
        if not separator or not local or not domain or "@" in domain or any(char.isspace() for char in address):
            print("Email handles must be valid comma-separated addresses.", file=sys.stderr)
            return None
        if address not in seen:
            seen.add(address)
            aliases.append(AliasInput(value=address, kind=AliasKind.HANDLE))
    return aliases


def _preflight_init_aliases(
    runtime: ApplicationRuntime,
    target: Person | None,
    aliases: list[AliasInput],
) -> bool:
    for alias in aliases:
        matches = runtime.repo.find_by_normalized_name(normalize_name(alias.value))
        if not matches:
            continue
        if target is not None and len(matches) == 1 and matches[0].id == target.id:
            continue
        print(f"Cannot continue: email handle {alias.value} identifies another or ambiguous person.", file=sys.stderr)
        return False
    return True


def _preflight_vcard_path(raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser().absolute()
    try:
        if not path.is_file():
            raise OSError("path is not a readable file")
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        print(f"Cannot read vCard file: {exc}", file=sys.stderr)
        return None
    return path


def _run_init_vcard_import(runtime: ApplicationRuntime, path: Path, self_person: Person) -> int:
    handles = [alias.value for alias in self_person.aliases if alias.kind == AliasKind.HANDLE]
    if not handles:
        print(
            "Warning: self has no email handle; email-based self-card exclusion is unavailable.",
            file=sys.stderr,
        )
    try:
        batch = runtime.use_cases.import_content.execute("vcard", path=str(path))
        review = runtime.use_cases.review_import.execute(batch.batch_id)
    except ImportPipelineError as exc:
        if exc.code == "no_candidates":
            print("No external vCard candidates found.")
            return 0
        print(f"vCard import failed: {exc}", file=sys.stderr)
        return 1
    except (ImportExtractionError, OSError) as exc:
        print(f"vCard import failed: {exc}", file=sys.stderr)
        return 1
    print_import_review(review.candidates)
    selected = [
        candidate_id.strip()
        for candidate_id in input("Candidate IDs to accept (comma-separated): ").split(",")
    ]
    accepted_ids = list(dict.fromkeys(candidate_id for candidate_id in selected if candidate_id))
    known_ids = {row.id for row in review.candidates}
    unknown_ids = sorted(set(accepted_ids) - known_ids)
    if unknown_ids:
        print("Unknown candidate IDs: " + ", ".join(unknown_ids), file=sys.stderr)
        return 2
    try:
        result = runtime.use_cases.commit_import.execute(batch.batch_id, accepted_ids)
    except ImportPipelineError as exc:
        print(f"vCard commit failed: {exc}", file=sys.stderr)
        return 1
    print(f"Committed {len(result.committed_ids)} import candidates; {len(result.unresolved_ids)} unresolved.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Create only the dedicated fictional demo database."""
    demo_path = _demo_db_path()
    if _existing_demo_files(demo_path) and not args.reset:
        print(f"Demo database already exists: {demo_path}. Use --reset to replace it.", file=sys.stderr)
        return 1
    if args.reset:
        _remove_demo_files(demo_path)
    runtime = build_runtime(
        demo_path,
        warning=lambda message: print(f"Warning: {message}", file=sys.stderr),
    )
    try:
        people = _seed_demo(runtime)
    finally:
        runtime.close()
    print_demo_instructions(demo_path, people)
    return 0


def _demo_db_path(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    data_home = values.get("XDG_DATA_HOME")
    if data_home:
        base = Path(data_home).expanduser()
    else:
        home = values.get("HOME")
        base = Path(home).expanduser() / ".local" / "share" if home else Path.home() / ".local" / "share"
    return Path(os.path.abspath(base / "people-context" / _DEMO_FILENAME))


def _existing_demo_files(demo_path: Path) -> list[Path]:
    return [path for path in _demo_files(demo_path) if os.path.lexists(path)]


def _remove_demo_files(demo_path: Path) -> None:
    for path in _demo_files(demo_path):
        path.unlink(missing_ok=True)


def _demo_files(demo_path: Path) -> tuple[Path, Path, Path]:
    return demo_path, Path(f"{demo_path}-wal"), Path(f"{demo_path}-shm")


def _seed_demo(runtime: ApplicationRuntime) -> dict[str, Person]:
    people: dict[str, Person] = {}
    for seed in DEMO_PEOPLE:
        aliases = [AliasInput(value=handle, kind=AliasKind.HANDLE) for handle in seed.handles]
        people[seed.key] = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name=seed.name,
                aliases=aliases,
                summary=seed.summary,
                is_self=seed.is_self,
                source="demo",
            )
        ).person
    for seed in DEMO_AFFILIATIONS:
        runtime.use_cases.set_affiliation.execute(
            SetAffiliationInput(
                person_id=people[seed.person_key].id,
                org=seed.organization,
                role=seed.role,
                source="demo",
            )
        )
    for seed in DEMO_FACTS:
        runtime.use_cases.record_fact.execute(
            RecordFactInput(
                person_id=people[seed.person_key].id,
                predicate=seed.predicate,
                value=seed.value,
                valid_from=seed.valid_from,
                source="demo",
            )
        )
    for seed in DEMO_INTERACTIONS:
        runtime.use_cases.record_interaction.execute(
            RecordInteractionInput(
                summary=seed.summary,
                participant_ids=[people[key].id for key in seed.participant_keys],
                occurred_at=seed.occurred_at,
                channel=seed.channel,
                source="demo",
            )
        )
    for seed in DEMO_RELATIONSHIPS:
        runtime.use_cases.set_relationship.execute(
            SetRelationshipInput(
                subject_id=people[seed.subject_key].id,
                object_id=people[seed.object_key].id,
                type=seed.relationship_type,
                source="demo",
            )
        )
    return people
