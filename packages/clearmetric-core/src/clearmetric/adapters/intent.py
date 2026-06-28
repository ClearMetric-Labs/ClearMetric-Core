"""Intent YAML adapter for metric and query contract nodes."""

from __future__ import annotations

from pathlib import Path

import yaml
from clearmetric.core import CatalogArtifact, Node
from clearmetric.core.errors import AdapterError
from clearmetric.core.ids import metric_id, query_id
from clearmetric.core.project import ClearMetricProject
from clearmetric.core.validate import collect_schema_errors


def ingest_intent(project: ClearMetricProject) -> CatalogArtifact:
    """Ingest intent YAML files into metric/query contract nodes."""
    intent_source = project.sources.intent
    if intent_source is None or not intent_source.paths:
        return CatalogArtifact()

    errors: list[str] = []
    nodes: list[Node] = []

    for path_str in intent_source.paths:
        path = Path(path_str)
        if path.is_dir():
            files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
        elif path.is_file():
            files = [path]
        else:
            errors.append(f"{path}: intent path not found")
            continue

        for file_path in files:
            try:
                raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                errors.append(f"{file_path}: invalid YAML: {exc}")
                continue

            if raw is None:
                continue
            if not isinstance(raw, dict):
                errors.append(f"{file_path}: intent file must be a mapping")
                continue

            file_errors = collect_schema_errors(raw, "intent.schema.json")
            for message in file_errors:
                errors.append(f"{file_path}: {message}")
            if file_errors:
                continue

            for metric in raw.get("metrics") or []:
                node_id = metric_id(metric["id"])
                nodes.append(
                    Node(
                        id=node_id,
                        kind="metric",
                        name=metric["name"],
                        qualified_name=metric["id"],
                        aspects={
                            "metric": {
                                "formula": metric["formula"],
                                "unit": metric.get("unit"),
                                "description": metric.get("description"),
                                "depends_on": metric.get("depends_on") or [],
                            }
                        },
                    )
                )

            for query in raw.get("queries") or []:
                node_id = query_id(query["id"])
                nodes.append(
                    Node(
                        id=node_id,
                        kind="query",
                        name=query["name"],
                        qualified_name=query["id"],
                        aspects={
                            "query": {
                                "sql": query["sql"],
                                "description": query.get("description"),
                                "depends_on": query.get("depends_on") or [],
                                "parameters": query.get("parameters") or {},
                            }
                        },
                    )
                )

    if errors:
        raise AdapterError("intent validation failed:\n" + "\n".join(errors))

    return CatalogArtifact(nodes=nodes)
