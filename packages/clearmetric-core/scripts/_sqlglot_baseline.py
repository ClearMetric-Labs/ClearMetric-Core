from __future__ import annotations

from pathlib import Path

from clearmetric.lineage import load_project
from sqlglot.lineage import Node as LineageNode
from sqlglot.lineage import lineage


def build_raw_downstream_index(
    *,
    manifest_path: Path,
    dialect: str,
) -> dict[str, list[str]]:
    project = load_project(manifest_path, dialect=dialect)
    typed_schema = project.typed_schema()
    downstream: dict[str, set[str]] = {}

    for dataset in project.datasets.values():
        if dataset.kind != "local" or not dataset.sql:
            continue
        for column_name in dataset.declared_columns:
            root = lineage(
                column_name,
                dataset.sql,
                schema=typed_schema,
                sources=project.sources_for(dataset.name),
                dialect=dialect,
            )
            for leaf_ref in collect_leaf_refs(root):
                downstream.setdefault(leaf_ref, set()).add(
                    f"{dataset.name}.{column_name}"
                )

    return {key: sorted(values) for key, values in sorted(downstream.items())}


def collect_leaf_refs(node: LineageNode) -> set[str]:
    if not node.downstream:
        return {node.name}
    refs: set[str] = set()
    for child in node.downstream:
        refs.update(collect_leaf_refs(child))
    return refs
