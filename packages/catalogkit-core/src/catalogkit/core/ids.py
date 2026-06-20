"""Canonical identifier normalization and ID builders."""

from __future__ import annotations

from collections.abc import Iterable

from .errors import CanonicalIdError

_QUOTE_PAIRS = {
    '"': '"',
    "`": "`",
    "[": "]",
}


def _strip_matching_quotes(value: str) -> str:
    if len(value) < 2:
        return value

    first = value[0]
    last = value[-1]
    expected_last = _QUOTE_PAIRS.get(first)
    if expected_last == last:
        return value[1:-1]
    return value


def normalize_identifier(value: str) -> str:
    """Normalize a possibly qualified identifier into canonical dotted form."""
    parts = split_qualified_identifier(value)
    return normalize_identifier_parts(parts)


def normalize_identifier_parts(parts: Iterable[str]) -> str:
    """Normalize already separated identifier parts into canonical dotted form."""
    normalized_parts = [normalize_identifier_part(part) for part in parts if str(part).strip()]
    if not normalized_parts:
        raise CanonicalIdError("Identifier must contain at least one non-empty part.")
    return ".".join(normalized_parts)


def normalize_identifier_part(part: str) -> str:
    """Normalize one identifier segment."""
    value = str(part).strip()
    if not value:
        raise CanonicalIdError("Identifier part cannot be empty.")
    if value == "*":
        raise CanonicalIdError("Wildcard identifiers cannot be canonicalized.")
    unquoted = _strip_matching_quotes(value).strip()
    if not unquoted:
        raise CanonicalIdError("Identifier part cannot be empty after unquoting.")
    return unquoted.lower()


def split_qualified_identifier(value: str) -> list[str]:
    """Split a qualified identifier on dots while respecting quoted segments."""
    text = str(value).strip()
    if not text:
        raise CanonicalIdError("Identifier cannot be empty.")

    parts: list[str] = []
    current: list[str] = []
    quote_stack: list[str] = []

    for char in text:
        if quote_stack:
            current.append(char)
            if char == quote_stack[-1]:
                quote_stack.pop()
            continue

        if char in _QUOTE_PAIRS:
            quote_stack.append(_QUOTE_PAIRS[char])
            current.append(char)
            continue

        if char == ".":
            part = "".join(current).strip()
            if not part:
                raise CanonicalIdError(f"Invalid qualified identifier {value!r}.")
            parts.append(part)
            current = []
            continue

        current.append(char)

    if quote_stack:
        raise CanonicalIdError(f"Unclosed quote in identifier {value!r}.")

    final_part = "".join(current).strip()
    if not final_part:
        raise CanonicalIdError(f"Invalid qualified identifier {value!r}.")
    parts.append(final_part)
    return parts


def table_id(qualified_name: str) -> str:
    return f"table:{normalize_identifier(qualified_name)}"


def cte_id(name: str) -> str:
    return f"cte:{normalize_identifier_part(name)}"


def column_id(parent_qualified_name: str, column_name: str) -> str:
    parent = normalize_identifier(parent_qualified_name)
    column = normalize_identifier_part(column_name)
    return f"column:{parent}.{column}"


def model_id(qualified_name: str) -> str:
    return f"model:{normalize_identifier(qualified_name)}"


def report_id(qualified_name: str) -> str:
    return f"report:{normalize_identifier(qualified_name)}"


def asset_id(qualified_name: str) -> str:
    return f"asset:{normalize_identifier(qualified_name)}"


def schema_name(qualified_name: str) -> str | None:
    normalized = normalize_identifier(qualified_name)
    parts = normalized.split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


def leaf_name(qualified_name: str) -> str:
    normalized = normalize_identifier(qualified_name)
    return normalized.split(".")[-1]


__all__ = [
    "asset_id",
    "column_id",
    "cte_id",
    "leaf_name",
    "model_id",
    "normalize_identifier",
    "normalize_identifier_part",
    "normalize_identifier_parts",
    "report_id",
    "schema_name",
    "split_qualified_identifier",
    "table_id",
]
