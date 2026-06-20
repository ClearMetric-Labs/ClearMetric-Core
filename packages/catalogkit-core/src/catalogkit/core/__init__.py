"""Public package surface for catalogkit-core."""

from __future__ import annotations

from ._version import __version__
from .errors import CanonicalIdError, CatalogCoreError, MergeConflictError
from .ids import (
    asset_id,
    column_id,
    cte_id,
    leaf_name,
    model_id,
    normalize_identifier,
    normalize_identifier_part,
    normalize_identifier_parts,
    report_id,
    schema_name,
    split_qualified_identifier,
    table_id,
)
from .merge import merge
from .models import CatalogArtifact, Edge, Evidence, Node, Warning
from .serialize import render_json

__all__ = [
    "__version__",
    "asset_id",
    "CatalogArtifact",
    "CatalogCoreError",
    "CanonicalIdError",
    "column_id",
    "cte_id",
    "Edge",
    "Evidence",
    "leaf_name",
    "merge",
    "MergeConflictError",
    "model_id",
    "Node",
    "normalize_identifier",
    "normalize_identifier_part",
    "normalize_identifier_parts",
    "render_json",
    "report_id",
    "schema_name",
    "split_qualified_identifier",
    "table_id",
    "Warning",
]
