# Structured person names

Add optional `preferred_name`, `given_name`, and `family_name` without splitting or replacing existing canonical names. Preferred name means how the user addresses someone; given/family names are explicit components, independent of name order. Empty input clears a component. Display uses preferred name, then given name, then canonical name.

Browse lists and sorts by that display name and retains the canonical name for disambiguation. All three fields are editable in browse and CLI and available through MCP and reviewed person candidates. Searching includes the fields; canonical names and aliases remain the identity matching keys so common name components do not silently change import matching.

## Approaches

Reusing nickname aliases would avoid columns but cannot distinguish a preferred name from other nicknames. Automatically splitting canonical names would invent boundaries. The chosen approach is explicit optional columns with an additive migration, preserving every existing identity and alias.

Persistence, audit snapshots, merges, portable exports, and bootstrap restore retain these fields. Sync bundles advance from version 7 to 8; older versions remain refused. vCard uses explicitly known given/family components and keeps the existing unstructured fallback when neither is known. No automatic personal-data cleanup or name guessing is included.
