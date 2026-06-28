"""Emitter registry."""

from __future__ import annotations

from collections.abc import Callable

from clearmetric.compiler.models import CompiledGraph
from clearmetric.core.errors import EmitterError
from clearmetric.policy import load_rules
from clearmetric.policy.models import PolicyRulesFile

from .catalog import emit_catalog
from .consumer_catalog import emit_consumer_catalog
from .frontend_contract import emit_frontend_contract
from .json import emit_json
from .openlineage import emit_openlineage
from .text import emit_text


def _require_identity(format: str, identity: str | None) -> str:
    if not identity:
        raise EmitterError(f"format {format!r} requires --identity")
    return identity


def _policy_rules(compiled: CompiledGraph) -> PolicyRulesFile:
    return load_rules(compiled.project.policy.rules)


def _emit_gated(
    format: str,
    compiled: CompiledGraph,
    *,
    identity: str | None,
    emit_fn: Callable[..., str],
) -> str:
    resolved = _require_identity(format, identity)
    rules = _policy_rules(compiled)
    return emit_fn(compiled, identity=resolved, rules=rules)


def emit_compile(
    format: str,
    compiled: CompiledGraph,
    *,
    identity: str | None = None,
) -> str:
    if format == "json":
        return emit_json(compiled)
    if format == "text":
        return emit_text(compiled)
    if format == "catalog":
        return emit_catalog(compiled)
    if format == "consumer-catalog":
        return _emit_gated(format, compiled, identity=identity, emit_fn=emit_consumer_catalog)
    if format == "frontend-contract":
        return _emit_gated(format, compiled, identity=identity, emit_fn=emit_frontend_contract)
    if format == "openlineage":
        return _emit_gated(format, compiled, identity=identity, emit_fn=emit_openlineage)
    if format == "ai-context":
        _require_identity(format, identity)
        raise EmitterError("ai-context emitter is not implemented yet")

    raise EmitterError(f"unsupported compile format: {format}")
