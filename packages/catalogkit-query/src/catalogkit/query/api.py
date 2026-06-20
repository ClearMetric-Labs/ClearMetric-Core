"""Public API for catalogkit-query."""

from __future__ import annotations

from catalogkit.core import CatalogArtifact

from .build import build_catalog_artifact_from_parsed, build_query_map_from_parsed
from .models import QueryMap
from .parser import parse_statement
from .render.json import render_json
from .render.text import render_text


def build_query_map(
    sql: str,
    *,
    dialect: str,
) -> QueryMap:
    """Build the public catalogkit-query artifact for one SQL statement."""
    parsed = parse_statement(sql, dialect=dialect)
    return build_query_map_from_parsed(parsed)


def build_catalog_artifact(
    sql: str,
    *,
    dialect: str,
) -> CatalogArtifact:
    """Build the shared catalog artifact for CatalogKit composition."""
    parsed = parse_statement(sql, dialect=dialect)
    return build_catalog_artifact_from_parsed(parsed)


__all__ = [
    "build_catalog_artifact",
    "build_query_map",
    "render_json",
    "render_text",
]
