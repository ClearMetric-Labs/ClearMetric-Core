"""Shared errors for clearmetric-core."""

from __future__ import annotations


class ClearMetricError(Exception):
    """Base class for clearmetric-core failures."""


class CanonicalIdError(ClearMetricError):
    """Raised when an identifier cannot be normalized into a canonical ID."""


class MergeConflictError(ClearMetricError):
    """Raised when artifacts cannot be merged without losing information."""


class AliasMapError(ClearMetricError):
    """Raised when a table alias file is invalid or unsupported."""


class ProjectConfigError(ClearMetricError):
    """Raised when clearmetric.yaml or project paths are invalid."""


class ValidationError(ClearMetricError):
    """Raised when JSON/YAML data fails schema validation."""


class AdapterError(ClearMetricError):
    """Raised when a source adapter fails ingestion."""


class CompilerError(ClearMetricError):
    """Raised when the compiler orchestration fails."""


class StructuralCheckError(ClearMetricError):
    """Raised when structural graph checks fail."""


class SecurityFloorError(ClearMetricError):
    """Raised when the security floor is violated."""


class PolicyError(ClearMetricError):
    """Raised when policy rules are invalid."""


class EmitterError(ClearMetricError):
    """Raised when output emission fails."""


class GraphError(ClearMetricError):
    """Raised when a graph lookup or traversal fails."""


class SelectorError(ClearMetricError):
    """Raised when a graph selector expression is invalid."""


class QueryExecutionError(ClearMetricError):
    """Raised when query execution fails."""
