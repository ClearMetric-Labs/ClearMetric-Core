"""Package-specific errors for catalogkit-query."""

from __future__ import annotations


class QueryMapError(Exception):
    """Base class for catalogkit-query failures."""


class QueryMapParseError(QueryMapError):
    """Raised when SQL cannot be parsed into a supported AST."""


class QueryMapContractError(QueryMapError):
    """Raised when parsed SQL cannot be represented by the current contract."""
