"""Graph selector grammar."""

from __future__ import annotations

from dataclasses import dataclass

from clearmetric.core.errors import SelectorError
from clearmetric.core.models import Node


@dataclass(frozen=True)
class SelectorPredicate:
    kind: str | None = None
    id_prefix: str | None = None


def parse_selector(expression: str) -> SelectorPredicate:
    text = expression.strip()
    if not text:
        raise SelectorError("Selector expression cannot be empty.")
    if text.startswith("kind:"):
        return SelectorPredicate(kind=text[len("kind:") :].strip())
    if text.startswith("id:"):
        return SelectorPredicate(id_prefix=text[len("id:") :].strip())
    if ":" not in text:
        return SelectorPredicate(kind=text)
    raise SelectorError(f"Unsupported selector expression: {expression!r}")


def matches_selector(selector: SelectorPredicate, node: Node) -> bool:
    if selector.kind is not None and node.kind != selector.kind:
        return False
    if selector.id_prefix is not None and not node.id.startswith(selector.id_prefix):
        return False
    return True
