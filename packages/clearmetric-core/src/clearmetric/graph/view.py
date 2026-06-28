"""Indexed read-only view over a catalog artifact."""

from __future__ import annotations

from dataclasses import dataclass

from clearmetric.core.errors import GraphError
from clearmetric.core.models import CatalogArtifact, Edge, Node


@dataclass(frozen=True)
class GraphView:
    artifact: CatalogArtifact
    _nodes_by_id: dict[str, Node]
    _edges_by_kind: dict[str, list[Edge]]

    @classmethod
    def from_artifact(cls, artifact: CatalogArtifact) -> GraphView:
        nodes_by_id = {node.id: node for node in artifact.nodes}
        edges_by_kind: dict[str, list[Edge]] = {}
        for edge in artifact.edges:
            edges_by_kind.setdefault(edge.kind, []).append(edge)
        return cls(
            artifact=artifact,
            _nodes_by_id=nodes_by_id,
            _edges_by_kind=edges_by_kind,
        )

    def node(self, node_id: str) -> Node:
        try:
            return self._nodes_by_id[node_id]
        except KeyError as exc:
            raise GraphError(f"Unknown node {node_id!r}") from exc

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes_by_id

    def nodes(self, *, kind: str | None = None) -> list[Node]:
        if kind is None:
            return list(self.artifact.nodes)
        return [node for node in self.artifact.nodes if node.kind == kind]

    def edges(self, *, kind: str | None = None) -> list[Edge]:
        if kind is None:
            return list(self.artifact.edges)
        return list(self._edges_by_kind.get(kind, ()))
