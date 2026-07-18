# Relationship graph

M7 turns relationship types into data, stores one canonical edge per assertion, and exposes bounded structural
traversal without disclosing a person's facts, traits, observations, or interaction history.

## Vocabulary model

Migration `003_relationship_vocabulary.sql` adds two tables:

- `relationship_types(type, inverse, symmetric, category, canonical)` describes direction and category.
- `relationship_type_synonyms(synonym, type)` maps normalized aliases to a vocabulary row.

Seeded reference data covers professional, family, and social relationships. Seed rows are recreated by the
migration on each device and do not produce audit or changelog operations. Custom rows created through the CLI
are portable user state and are captured as `entity_type = relationship_type` writes.

Inverse pairs have two vocabulary rows. Exactly one is canonical: for example, `reports_to` is canonical and
`manages` points back to it with `canonical = 0`. Symmetric types such as `friend_of` have no inverse.

Unknown normalized types remain legal. They are stored without creating vocabulary and are reported as
`uncategorized` when read.

## Write-time normalization

`set_relationship` applies these rules before storage:

1. Unicode-normalize and snake-case the supplied type (`manager of` becomes `manager_of`).
2. Resolve a synonym when one exists.
3. For a non-canonical inverse row, swap subject and object and store the canonical type.
4. For a symmetric type, order endpoint ids lexically.
5. If the same active canonical `(subject_id, object_id, type)` already exists, update that edge's label,
   validity period, and confidence instead of inserting another row.

Only one edge is stored. The inverse is always derived at read time. All creates, deduplicating updates, custom
vocabulary writes, and explicit normalization rewrites use the M6 transactional `audit_mutation` seam, so
primary state, audit, HLC advancement, and changelog capture remain atomic.

Existing pre-M7 rows are not rewritten by migration 003. Use the curation command described below so rewrites
are auditable and replayable.

## Perspective rendering

Hydrated relationship records include additive `display_type`:

- queried person is the stored subject: use the stored canonical type;
- queried person is the object of an inverse pair: use the inverse type;
- symmetric type: use the stored type from either endpoint;
- uncategorized type: use the stored type as a fallback.

For a stored `A reports_to B` edge, A sees `reports_to` and B sees `manages`. The stored `relationship.type`
field is unchanged. Person context, communication guidance, CLI `show`, path results, and vault links all use
the same perspective rule.

## Graph traversal

The `GraphReader` port is deliberately narrow:

```python
neighbors(person_id, depth)
path_between(a, b, max_depth)
subgraph(person_ids, depth)
```

The SQLite adapter uses cycle-safe recursive CTEs over active relationships whose two endpoint people are not
soft-deleted. Ordering is deterministic, with edge id used to break traversal ties.

Application use cases enforce all disclosure caps:

- depth defaults to 2 and may not exceed 4;
- at most 100 nodes;
- at most 300 edges.

When a cap removes data, `truncated` is `true`; truncation is never silent.

### `get_relationship_graph`

`get_relationship_graph(person_id, depth=2, types=None)` returns only structural data:

- nodes: `person_id`, `name`, `is_self`;
- edges: canonical `subject_id`, `object_id`, `type`, optional `label`, and `category`;
- `truncated`.

It intentionally omits summaries, facts, traits, observations, interactions, and reminders. An unknown or
soft-deleted id returns `{"error": "person_not_found", "person_id": ...}`.

### `find_connection`

`find_connection(person_a, person_b, max_depth=4)` returns one deterministic shortest path. Each ordered hop
contains the destination `person` and connecting `edge`; the edge adds `display_type` from the preceding
person's perspective. A disconnected pair returns:

```json
{"connected": false, "hops": [], "reason": "not_connected"}
```

Both MCP tools are annotated `readOnlyHint=true`.

## Curation workflow

List seeded/custom vocabulary and uncategorized types currently in use:

```bash
uv run people-context relationship-types
```

Add a custom symmetric type and synonyms:

```bash
uv run people-context relationship-types add co_founder_of \
  --category professional --symmetric --synonym cofounder
```

Add an inverse pair:

```bash
uv run people-context relationship-types add advises \
  --category professional --inverse advised_by --synonym advisor_of
```

Vocabulary is add-only in v1; edit and delete are intentionally absent.

Preview legacy edge canonicalization without writing:

```bash
uv run people-context normalize-relationships
```

Apply the reported changes:

```bash
uv run people-context normalize-relationships --apply
```

If canonicalization makes two rows duplicates, the older relationship id is retained and the newer row is
removed. Both the rewrite and removal are represented in audit and changelog capture.
