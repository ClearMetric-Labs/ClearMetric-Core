# Understanding the outputs

All public emitters read the **same compiled graph** after build + enforce.

Format and flag reference: [Input and output formats](../reference/io-formats.md) · [CLI reference](../reference/cli.md).

## Typical workflow

```bash
cm compile --format json > graph.json
cm compile --format catalog > catalog.json
cm compile --format openlineage > openlineage.json

cm impact COLUMN --upstream --format json
cm impact COLUMN --downstream --format json

cm clean              # report findings; exit 1 on severity error only
cm contract graph.json  # CI gate: schema + enforce
```

Impact JSON includes `related_ids`, `traversed_edges`, and warnings scoped to the traversal.

## Schema detail

Artifact shape and ID rules: [Artifact contract](../reference/contract.md).
