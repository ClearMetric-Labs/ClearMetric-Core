# catalogkit-core Contract

`catalogkit-core` owns the serialized artifact contract used by CatalogKit tools
and downstream consumers.

## Artifact Version

- `version` means artifact schema version only.
- `version` is owned by `catalogkit-core`.
- `version` is decoupled from package versions for `catalogkit-core`,
  `catalogkit-query`, and every future CatalogKit package.
- Bump `version` only for breaking changes to the serialized artifact contract.
- Non-breaking additions must not bump `version`.

Current artifact schema version: `1`

## Artifact Shape

Every serialized artifact has this shape:

```text
CatalogArtifact
  version
  nodes
  edges
  warnings
```

## Node Contract

Every node has:

- `id`
- `kind`
- `name`
- `qualified_name`
- `schema`
- `evidence`

`kind` is one of:

- `table`
- `cte`
- `column`
- `model`
- `report`
- `asset`

## Edge Contract

Every edge has:

- `kind`
- `source_id`
- `target_id`
- `label`
- `confidence`
- `evidence`

`kind` is one of:

- `depends_on`
- `feeds`
- `derives_from`
- `references`
- `joins`

## Evidence Contract

Evidence is where tools attach supporting observations without changing entity
identity.

Every evidence entry has:

- `file`
- `location`
- `expression`
- `confidence`

Multiple tools may attach evidence to the same node or edge. That is the normal
composition path.

## Merge Semantics

`merge()` is a contract feature of `catalogkit-core`, not a tool-local policy.

### Nodes

- Nodes merge by canonical `id`.
- Same `id` means the two nodes claim to describe the same entity.
- Evidence is unioned across matching nodes.
- Non-evidence attributes are merged field by field.
- If one side provides a value and the other side leaves that field empty, the
  populated value wins.
- If both sides provide non-empty different values for the same non-evidence
  field, merge fails with a typed error. There is no last-write-wins behavior.

### Edges

- Edges dedupe by `(kind, source_id, target_id)`.
- Evidence is unioned across matching edges.
- For other edge fields such as `label` and `confidence`, a populated value may
  fill an empty value.
- Conflicting non-empty edge attributes fail with a typed error.

### Warnings

- Warnings dedupe by their stable structural fields: `code`, `message`, and
  `location`.

## Canonical ID Rules

Canonical IDs are the highest-risk correctness surface in CatalogKit. If tools
normalize identifiers differently, graphs do not merge.

### Normalization Rules

- Identifier normalization is defined once in `catalogkit-core`.
- Tools must reuse `catalogkit-core` normalization helpers and must not implement
  local copies.
- Identifier parts are trimmed, unquoted, and lowercased before ID generation.
- Equivalent names such as `Analytics.Orders`, `analytics.orders`, and
  `"analytics"."orders"` must normalize to the same canonical name:
  `analytics.orders`.
- Empty identifier parts are invalid and must fail loudly.

### Canonical ID Forms

- table: `table:<normalized qualified name>`
- cte: `cte:<normalized name>`
- column: `column:<normalized qualified parent>.<normalized column name>`
- model: `model:<normalized qualified name>`
- report: `report:<normalized qualified name>`
- asset: `asset:<normalized qualified name>`

Examples:

- `table:analytics.orders`
- `cte:customer_rollup`
- `column:analytics.orders.id`

If a tool cannot emit a valid canonical ID under these rules, it must fail
loudly instead of emitting a divergent ID.
