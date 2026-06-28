"""CheckSpec registry for cleaner checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from clearmetric.graph import GraphView, matches_selector, parse_selector, view_of

from .checks import (
    check_duplicate_bindings,
    check_edges_resolve,
    check_partial_derivation,
    check_unique_node_ids,
)
from .hygiene import check_duplicate_formula
from .models import Finding


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    tier: str
    fn: Callable[[GraphView], list[Finding]]
    selector: str | None = None


def _wrap_with_selector(
    spec: CheckSpec,
) -> Callable[[GraphView], list[Finding]]:
    if spec.selector is None:
        return spec.fn

    predicate = parse_selector(spec.selector)

    def run(view: GraphView) -> list[Finding]:
        from clearmetric.core.models import CatalogArtifact

        subset = CatalogArtifact(
            nodes=[node for node in view.nodes() if matches_selector(predicate, node)]
        )
        return spec.fn(view_of(subset))

    return run


CHECK_SPECS: list[CheckSpec] = [
    CheckSpec("check.unique_node_ids", "structural", check_unique_node_ids),
    CheckSpec("check.edges_resolve", "structural", check_edges_resolve),
    CheckSpec("check.duplicate_bindings", "error", check_duplicate_bindings),
    CheckSpec("check.partial_derivation", "warn", check_partial_derivation),
    CheckSpec(
        "check.duplicate_formula",
        "warn",
        check_duplicate_formula,
        selector="kind:metric",
    ),
]

CHECKS: list[Callable[[GraphView], list[Finding]]] = [
    _wrap_with_selector(spec) for spec in CHECK_SPECS
]
