"""Shared errors for catalogkit-core."""

from __future__ import annotations


class CatalogCoreError(Exception):
    """Base class for catalogkit-core failures."""


class CanonicalIdError(CatalogCoreError):
    """Raised when an identifier cannot be normalized into a canonical ID."""


class MergeConflictError(CatalogCoreError):
    """Raised when artifacts cannot be merged without losing information."""
