"""Public package surface for catalogkit-query."""

from __future__ import annotations

from catalogkit.core import CatalogArtifact

from ._version import __version__
from .api import build_catalog_artifact, build_query_map, render_json, render_text
from .errors import QueryMapContractError, QueryMapError, QueryMapParseError
from .models import (
    OutputColumn,
    OutputSourceHint,
    QueryMap,
    QuerySummary,
    Relation,
    RelationEdge,
    RelationUsage,
    WarningEntry,
)

__all__ = [
    "__version__",
    "build_catalog_artifact",
    "build_query_map",
    "CatalogArtifact",
    "OutputColumn",
    "OutputSourceHint",
    "QueryMap",
    "QuerySummary",
    "QueryMapContractError",
    "QueryMapError",
    "QueryMapParseError",
    "Relation",
    "RelationEdge",
    "RelationUsage",
    "render_json",
    "render_text",
    "WarningEntry",
]
