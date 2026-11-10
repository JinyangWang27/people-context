# Structured person names

Add optional `preferred_name`, `given_name`, and `family_name` without splitting or replacing existing canonical names. Preferred name means how the user addresses someone; given/family names are explicit components, independent of name order. Empty input clears a component. Display uses preferred name, then given name, then canonical name.

Browse lists and sorts by that display name and retains the canonical name for disambiguation. All three fields are editable in browse and CLI and available through MCP and reviewed person candidates. Searching includes the fields; canonical names and aliases remain the identity matching keys so common name components do not silently change import matching.

## Approaches

Reusing nickname aliases would avoid columns but cannot distinguish a preferred name from other nicknames. Automatically splitting canonical names would invent boundaries. The chosen approach is explicit optional columns with an additive migration, preserving every existing identity and alias.

Persistence, audit snapshots, merges, portable exports, and bootstrap restore retain these fields. Sync bundles advance from version 7 to 9; older versions remain refused. vCard uses explicitly known given/family components and keeps the existing unstructured fallback when neither is known. No automatic personal-data cleanup or name guessing is included.

## Approved follow-up: generated full names and Chinese names

The person view puts Preferred name first and shows Full name as read-only. Native expandable Name details
contains given/family editors and `family_name_first: bool = False`. With both components known, full name
is generated in the selected order; Han-character-only components concatenate without a space, and other
components join with a space. Compound family names are not split. Incomplete components fall back to the
existing canonical name, preserved for compatibility, imports and identity matching. No generated full-name
column is stored. Preferred name and explicit aliases remain independent.

Chinese example: canonical name 王小明, preferred/given name 小明, family name 王, family_name_first true,
and explicit transliteration aliases Xiaoming and Wang Xiaoming. Conversation resolves either alias while
browse displays 小明; shared aliases remain ambiguous. No automatic transliteration is introduced.

Migration 012 stores the order flag, defaults existing rows to false, and preserves all data. The strict sync
bundle advances to version 9 to carry the flag. Existing canonical-name APIs remain compatible; index and
context projections add a derived full_name and the order flag. vCard writes the derived full name, keeps N
components, and recognizes explicit family-first FN order when it exactly matches those components.
