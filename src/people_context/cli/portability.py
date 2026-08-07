"""Database path, export, and sync-bundle CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from people_context.adapters.filesystem.private_file import atomic_write_private_text
from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.exports import SYNC_BUNDLE_FILENAME, render_bundle_json
from people_context.config import describe_resolution, resolve_db_path
from people_context.domain.sync_bundle import SyncBundleError
from people_context.ports.vault import VaultSafetyError

PLAINTEXT_BUNDLE_WARNING = (
    "This bundle is plaintext personal data, audit history, and replay payloads. "
    "Keep and transport it only on encrypted storage or through an encrypted channel."
)
BASELINE_TARGET_NOTICE = (
    "Restore only ever fills a freshly initialized database. It never merges with, clears, "
    "or overwrites existing local data."
)
# SQLite keeps these alongside the main database file; replacing any of them loses data.
_DATABASE_SIDECARS = ("-wal", "-shm", "-journal")
REMINDER_CALENDAR_WARNING = (
    "This calendar file is plaintext personal data outside the server's disclosure controls. "
    "Keep it on encrypted storage and hand it to a calendar application only deliberately."
)


def cmd_db_path(args: argparse.Namespace) -> int:
    """Print the resolved database path or full trace."""
    if args.verbose:
        for line in describe_resolution(args.db):
            print(line)
    else:
        print(resolve_db_path(args.db))
    return 0


def cmd_export(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Export stable JSON to stdout or an owner-readable file."""
    document = runtime.use_cases.export_data.execute().model_dump(mode="json")
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.output:
        atomic_write_private_text(args.output, text + "\n")
    else:
        print(text)
    return 0


def cmd_reminders_ics(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Write active dated reminders as one owner-only iCalendar file."""
    destination = Path(args.output).expanduser()
    if _collides_with_database(destination, runtime.path):
        # Publication replaces the destination's directory entry while SQLite still holds
        # the old inode open, so writing over the live database or one of its sidecars
        # would silently destroy the store when the connection closes.
        print(
            f"Refusing to write the reminder calendar to {destination}: "
            f"it is the database this command is reading, or one of its sidecar files.",
            file=sys.stderr,
        )
        return 2
    result = runtime.use_cases.export_reminder_calendar.execute()
    try:
        written = atomic_write_private_text(destination, result.calendar)
    except OSError as exc:
        print(f"Cannot write the reminder calendar to {destination}: {exc.strerror or exc}", file=sys.stderr)
        return 1

    print(f"Wrote {result.exported} reminder(s) to {written}.")
    if result.skipped_undated:
        print(f"Skipped {result.skipped_undated} reminder(s) without a due date.")
    if result.skipped_naive_datetime:
        # The write contract still accepts naive datetimes; guessing a timezone here could
        # move a reminder across a day boundary, so the row is reported instead.
        print(f"Skipped {result.skipped_naive_datetime} reminder(s) whose stored timestamps have no timezone.")
    if result.recurrence_omitted:
        print(f"Exported {result.recurrence_omitted} reminder(s) with the recurrence rule omitted.")
    print(REMINDER_CALENDAR_WARNING)
    return 0


def _collides_with_database(destination: Path, database: Path) -> bool:
    """Return whether publishing at `destination` would replace a live database entry.

    The destination is compared as its own final directory entry, because atomic
    publication replaces that entry rather than following it: an output symlink pointing
    somewhere unrelated is replaced harmlessly, and a second hard link to the database
    keeps the data alive.

    Both the database path as given and its fully resolved target are reserved. SQLite
    follows a symlinked `--db`, so the entry holding the data is the resolved one, and
    naming that target as the output would destroy the store even though the two spellings
    differ. Reserving the given spelling too costs only a symlink the user was about to
    overwrite with a calendar anyway. WAL, shared-memory, and rollback sidecars carry the
    same loss, so they are reserved for both spellings as well.
    """
    reserved: set[tuple[str, str]] = set()
    for candidate in (database, Path(os.path.realpath(database))):
        reserved.add(_entry_identity(candidate))
        reserved.update(
            _entry_identity(candidate.with_name(candidate.name + suffix)) for suffix in _DATABASE_SIDECARS
        )
    return _entry_identity(destination) in reserved


def _entry_identity(path: Path) -> tuple[str, str]:
    """Identify one directory entry by its resolved parent and its own final name."""
    return os.path.realpath(path.parent), path.name


def cmd_sync_push(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Write one complete bootstrap bundle as an owner-only file."""
    document = runtime.use_cases.export_sync_bundle.execute()
    directory = Path(args.output).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        destination = atomic_write_private_text(directory / SYNC_BUNDLE_FILENAME, render_bundle_json(document))
    except OSError as exc:
        print(f"Cannot write the sync bundle to {directory}: {exc.strerror or exc}", file=sys.stderr)
        return 1

    snapshot = document.snapshot
    vocabulary = document.relationship_vocabulary
    print(f"Wrote sync bundle to {destination}.")
    print(
        f"People {len(snapshot.people)}, organizations {len(snapshot.organizations)}, "
        f"affiliations {len(snapshot.affiliations)}, relationships {len(snapshot.relationships)}, "
        f"facts {len(snapshot.facts)}, observations {len(snapshot.observations)}, "
        f"traits {len(snapshot.traits)}, interactions {len(snapshot.interactions)}, "
        f"reminders {len(snapshot.reminders)}, preferences {len(snapshot.user_preferences)}, "
        f"audit entries {len(snapshot.audit_log)}."
    )
    print(
        f"Relationship types {len(vocabulary.types)}, synonyms {len(vocabulary.synonyms)}, "
        f"devices {len(document.devices)}, changelog entries {len(document.changelog)}."
    )
    print(
        f"Origin device {document.origin_device_id} at watermark "
        f"{document.watermark.hlc_physical_ms}/{document.watermark.hlc_logical}."
    )
    print(PLAINTEXT_BUNDLE_WARNING)
    return 0


def cmd_sync_pull(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Restore one bootstrap bundle into a freshly initialized database."""
    source = Path(args.input).expanduser()
    if source.is_dir():
        source = source / SYNC_BUNDLE_FILENAME
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Cannot read the sync bundle at {source}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print(f"Cannot read the sync bundle at {source}: the file is not UTF-8 text.", file=sys.stderr)
        return 1

    restore = runtime.use_cases.restore_sync_bundle
    # The complete document is parsed and validated before anything is previewed or prompted,
    # so an unusable bundle can never reach a confirmation prompt or the destination.
    try:
        document = restore.parse(text)
    except SyncBundleError as exc:
        return _report_refusal(source, exc)

    preview = restore.preview(document)
    print(f"Restore {source} into {runtime.path}?")
    print(f"Bundle created {preview.created_at.isoformat()} on origin device {preview.origin_device_id}.")
    for label, count in preview.counts.items():
        print(f"  {label}: {count}")
    print(f"Bundle watermark {preview.watermark[0]}/{preview.watermark[1]}.")
    print(BASELINE_TARGET_NOTICE)
    if not args.yes and input("Proceed? [y/N] ").strip().casefold() not in {"y", "yes"}:
        print("Aborted.")
        return 0

    try:
        outcome = restore.execute(document)
    except SyncBundleError as exc:
        return _report_refusal(source, exc)

    print(f"Restored {outcome.people} people and {outcome.changelog_entries} changelog entries.")
    print(
        f"Imported devices {outcome.devices} (all retired), new relationship types "
        f"{outcome.relationship_types}, new synonyms {outcome.relationship_synonyms}, "
        f"indexed names {outcome.indexed_names}."
    )
    print(
        f"Local clock advanced to {outcome.local_watermark.physical_ms}/"
        f"{outcome.local_watermark.logical_counter}; this device keeps its own identity."
    )
    return 0


def _report_refusal(source: Path, error: SyncBundleError) -> int:
    """Print a structured refusal that names reasons but never record contents."""
    print(f"Refusing to restore {source} ({error.code}):", file=sys.stderr)
    for detail in error.details:
        print(f"  {detail}", file=sys.stderr)
    print("No changes were made.", file=sys.stderr)
    return 1


def cmd_export_vault(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Export an Obsidian relationship vault."""
    try:
        result = runtime.use_cases.export_vault.execute(
            args.output,
            include_sensitive=args.include_sensitive,
        )
    except VaultSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Exported {result.people} people and {result.organizations} organizations "
        f"to {result.output} ({result.files} files)."
    )
    return 0
