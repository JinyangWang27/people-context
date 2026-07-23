"""Relationship vocabulary and normalization CLI commands."""

from __future__ import annotations

import argparse
import sys

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.relationships import (
    AddRelationshipTypeInput,
    RelationshipTypeAlreadyExistsError,
)
from people_context.cli.rendering import print_table


def cmd_relationship_types(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """List relationship vocabulary or add one custom type."""
    if args.relationship_types_command == "add":
        try:
            rows = runtime.use_cases.add_relationship_type.execute(
                AddRelationshipTypeInput(
                    type=args.type,
                    category=args.category,
                    inverse=args.inverse,
                    symmetric=args.symmetric,
                    synonyms=args.synonym,
                )
            )
        except (RelationshipTypeAlreadyExistsError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Added relationship vocabulary: " + ", ".join(row.type for row in rows))
        return 0
    rows = runtime.relationship_vocabulary.list_types()
    print_table(
        ["TYPE", "INVERSE", "SYMMETRIC", "CATEGORY", "CANONICAL", "SYNONYMS"],
        [
            (
                row.type,
                row.inverse or "-",
                "yes" if row.symmetric else "no",
                row.category,
                "yes" if row.canonical else "no",
                ", ".join(row.synonyms) or "-",
            )
            for row in rows
        ],
    )
    print("\nUncategorized types in use:")
    uncategorized = runtime.relationship_vocabulary.list_uncategorized_types()
    if not uncategorized:
        print("  (none)")
    else:
        for type_name in uncategorized:
            print(f"  - {type_name}")
    return 0


def cmd_normalize_relationships(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Preview or apply canonical relationship rewrites."""
    result = runtime.use_cases.normalize_relationships.execute(apply=args.apply)
    if not result.changes:
        print("No relationship normalization changes.")
        return 0
    print("Applied relationship normalization:" if result.applied else "Dry run; relationship normalization would:")
    for change in result.changes:
        if change.action == "update" and change.after is not None:
            print(
                f"  update {change.relationship_id}: "
                f"{change.before.subject_id} {change.before.type} {change.before.object_id} -> "
                f"{change.after.subject_id} {change.after.type} {change.after.object_id}"
            )
        else:
            print(f"  merge {change.relationship_id} into {change.merged_into}")
    if not result.applied:
        print("Run again with --apply to execute these audited rewrites.")
    return 0
