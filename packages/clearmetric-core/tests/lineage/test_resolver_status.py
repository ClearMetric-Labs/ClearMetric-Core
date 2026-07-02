from __future__ import annotations

from clearmetric.core.models import Warning
from clearmetric.lineage.resolver_status import (
    ResolverStatusInput,
    derive_resolver_status,
    derive_type_status,
)


def test_zero_edges_with_schema_missing_is_valid() -> None:
    aspect = derive_resolver_status(
        ResolverStatusInput(
            edge_count=0,
            declared_column_count=5,
            output_column_count=0,
            warnings=(),
            type_status="missing",
            schema_names_available=False,
            schema_types_available=False,
            schema_source="manifest",
        )
    )
    assert aspect.resolver_status == "schema_missing"
    assert aspect.unknown_edges_possible is True
    assert aspect.blocking_findings


def test_complete_when_edges_and_no_blockers() -> None:
    aspect = derive_resolver_status(
        ResolverStatusInput(
            edge_count=3,
            declared_column_count=3,
            output_column_count=3,
            warnings=(),
            type_status="typed",
            schema_names_available=True,
            schema_types_available=True,
            schema_source="manifest",
        )
    )
    assert aspect.resolver_status == "complete"
    assert aspect.unknown_edges_possible is False


def test_partial_when_unresolved_output_source() -> None:
    aspect = derive_resolver_status(
        ResolverStatusInput(
            edge_count=1,
            declared_column_count=3,
            output_column_count=3,
            warnings=(
                Warning(
                    code="unresolved_output_source",
                    message="x",
                    subject_id="column:db.s.m.c",
                ),
            ),
            type_status="names_only",
            schema_names_available=True,
            schema_types_available=False,
            schema_source="inferred",
        )
    )
    assert aspect.resolver_status == "partial"
    assert "partial_unresolved_output" in aspect.blocking_findings


def test_type_status_derivation() -> None:
    assert (
        derive_type_status(
            column_names=("a", "b"), column_types={"a": "text", "b": "text"}
        )
        == "typed"
    )
    assert derive_type_status(column_names=("a",), column_types={}) == "names_only"
