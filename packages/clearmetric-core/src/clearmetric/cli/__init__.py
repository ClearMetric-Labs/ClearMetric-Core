"""ClearMetric Core CLI — ``cm`` command router."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from clearmetric.compiler import clean as run_clean
from clearmetric.compiler import compile as run_compile
from clearmetric.compiler import discover
from clearmetric.compiler import impact as run_impact
from clearmetric.compiler.impact import impact_from_artifact
from clearmetric.compiler.validate import enforce_graph
from clearmetric.core import ClearMetricError, __version__, load_artifact_file
from clearmetric.emitters import emit_compile, emit_impact
from clearmetric.emitters.diagnostics import format_compile_diagnostics

from .experimental import (
    compile_format_choices,
    is_experimental_enabled,
    is_lab_compile_format,
    require_experimental,
    require_gated_compile,
    require_query_identity,
)


def _build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cm",
        description="ClearMetric Core — warehouse-aware lineage compiler and graph engine.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cm {__version__} (ClearMetric Core)",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project directory containing clearmetric.yaml (default: .)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize a ClearMetric project.")

    connect = subparsers.add_parser(
        "connect", help="Configure warehouse metadata source."
    )
    connect_sub = connect.add_subparsers(dest="connect_target", required=True)
    warehouse = connect_sub.add_parser(
        "warehouse",
        help="Configure credential-free warehouse metadata fixture.",
    )
    warehouse.add_argument(
        "--information-schema",
        required=True,
        help="Path to local INFORMATION_SCHEMA JSON metadata export.",
    )

    scan = subparsers.add_parser("scan", help="Discover configured project sources.")
    scan.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format (default: text).",
    )

    compile_choices = compile_format_choices()
    compile_help = "Output format (default: json)."
    if is_experimental_enabled():
        compile_help += " Experimental formats require CM_EXPERIMENTAL=1."

    compile_parser = subparsers.add_parser(
        "compile", help="Compile project into a graph."
    )
    compile_parser.add_argument(
        "--format",
        choices=compile_choices,
        default="json",
        help=compile_help,
    )
    if is_experimental_enabled():
        compile_parser.add_argument(
            "--identity",
            help="[experimental] Identity for gated lab formats (consumer-catalog, frontend-contract, ai-context).",
        )

    impact_parser = subparsers.add_parser(
        "impact",
        help="Trace upstream or downstream column lineage for one selection.",
    )
    impact_parser.add_argument(
        "selection",
        help="Column selection, for example orders.amount.",
    )
    traversal = impact_parser.add_mutually_exclusive_group(required=True)
    traversal.add_argument("--upstream", action="store_true")
    traversal.add_argument("--downstream", action="store_true")
    impact_parser.add_argument(
        "--format",
        choices=("text", "json", "mermaid"),
        default="text",
        help="Output format (default: text).",
    )
    impact_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Use a persisted CatalogArtifact JSON instead of compiling.",
    )
    if is_experimental_enabled():
        impact_parser.add_argument(
            "--identity",
            help="[experimental] Governance preview: filter traversal to nodes visible to identity.",
        )

    if is_experimental_enabled():
        query_parser = subparsers.add_parser(
            "query",
            help="[experimental] Execute a compiled query contract via DuckDB.",
        )
        query_parser.add_argument(
            "--identity",
            required=True,
            help="[experimental] Identity for policy gate before query execution.",
        )
        query_parser.add_argument(
            "query_id",
            help="Query node id, for example query.executive_revenue or query:executive_revenue.",
        )

        serve_parser = subparsers.add_parser(
            "serve",
            help="[experimental] Localhost-only single-identity query debug harness (not an auth server).",
        )
        serve_parser.add_argument(
            "artifact_path",
            help="Path to compiled graph JSON.",
        )
        serve_parser.add_argument(
            "--identity",
            required=True,
            help="[experimental] Server-bound identity for gated query execution (local debug only).",
        )
        serve_parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Loopback host only (default: 127.0.0.1).",
        )
        serve_parser.add_argument(
            "--port",
            type=int,
            default=8765,
            help="Port (default: 8765).",
        )

    clean_parser = subparsers.add_parser(
        "clean",
        help="Run compile checks and print a cleaner report.",
    )
    clean_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format (default: text).",
    )

    contract_parser = subparsers.add_parser(
        "contract",
        help="Validate a compiled artifact against the contract schema.",
    )
    contract_parser.add_argument("artifact_path", help="Path to compiled graph JSON.")

    return parser


def _project_dir(args: argparse.Namespace) -> Path:
    return Path(args.project_dir).expanduser().resolve()


def _run_init(args: argparse.Namespace) -> int:
    root = _project_dir(args)
    config_path = root / "clearmetric.yaml"
    if config_path.exists():
        print(f"cm error: project already exists: {config_path}", file=sys.stderr)
        return 1

    manifest = root / "target" / "manifest.json"
    sql_dir = root / "sql"
    warehouse_schema = root / "warehouse_schema.json"
    sources: dict = {
        "dbt": {"manifest": "./target/manifest.json"},
        "sql": {"paths": []},
    }
    if warehouse_schema.exists():
        sources = {
            "warehouse": {
                "kind": "information_schema",
                "path": "./warehouse_schema.json",
            },
            **sources,
        }

    if not manifest.exists() and not sql_dir.is_dir() and "warehouse" not in sources:
        print(
            "cm error: no target/manifest.json, sql/, or warehouse_schema.json found to initialize from",
            file=sys.stderr,
        )
        return 1

    if not manifest.exists():
        sources.pop("dbt", None)

    policy_dir = root / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    rules_path = policy_dir / "rules.yaml"
    rules_path.write_text("rules: []\n", encoding="utf-8")

    config_body = {
        "version": 1,
        "dialect": "postgres",
        "sources": sources,
        "posture": "strict",
        "policy": {"rules": "./policy/rules.yaml"},
    }
    config_path.write_text(
        yaml.safe_dump(config_body, sort_keys=False), encoding="utf-8"
    )

    gitignore = root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".clearmetric/" not in content:
            gitignore.write_text(
                content.rstrip() + "\n.clearmetric/\n", encoding="utf-8"
            )
    else:
        gitignore.write_text(".clearmetric/\n", encoding="utf-8")

    print(f"Initialized ClearMetric project at {config_path}")
    return 0


def _run_connect(args: argparse.Namespace) -> int:
    root = _project_dir(args)
    config_path = root / "clearmetric.yaml"
    if not config_path.exists():
        print(f"cm error: project config not found: {config_path}", file=sys.stderr)
        return 1

    if args.connect_target != "warehouse":
        print(
            f"cm error: unknown connect target {args.connect_target!r}; "
            "use: cm connect warehouse --information-schema PATH",
            file=sys.stderr,
        )
        return 1

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = raw.setdefault("sources", {})
    resolved = (root / args.information_schema).resolve()
    if not resolved.is_file():
        print(
            f"cm error: information schema file not found: {resolved}",
            file=sys.stderr,
        )
        return 1
    try:
        path_value = f"./{resolved.relative_to(root).as_posix()}"
    except ValueError:
        path_value = str(resolved)
    sources["warehouse"] = {
        "kind": "information_schema",
        "path": path_value,
    }

    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(f"Updated warehouse source in {config_path}")
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    report = discover(_project_dir(args))
    if args.format == "json":
        print(
            json.dumps(
                {
                    "config_path": report.config_path,
                    "dialect": report.dialect,
                    "sources": [
                        {"kind": source.kind, "path": source.path}
                        for source in report.sources
                    ],
                },
                indent=2,
                sort_keys=False,
            )
        )
        return 0
    print(f"config: {report.config_path}")
    print(f"dialect: {report.dialect}")
    for source in report.sources:
        print(f"source: {source.kind} -> {source.path}")
    return 0


def _run_compile(args: argparse.Namespace) -> int:
    identity = getattr(args, "identity", None)
    if is_lab_compile_format(args.format):
        identity = require_gated_compile(args.format, identity)
    compiled = run_compile(_project_dir(args))
    output = emit_compile(args.format, compiled, identity=identity)
    warning_block = format_compile_diagnostics(compiled.artifact)
    if warning_block:
        print(warning_block, file=sys.stderr)
    print(output)
    return 0


def _run_impact(args: argparse.Namespace) -> int:
    direction = "upstream" if args.upstream else "downstream"
    identity = getattr(args, "identity", None)
    if identity is not None:
        require_experimental("cm impact --identity")
    artifact_path = getattr(args, "artifact", None)
    if artifact_path is not None:
        compiled, result = impact_from_artifact(
            artifact_path,
            _project_dir(args),
            selection=args.selection,
            direction=direction,
            identity=identity,
        )
    else:
        compiled, result = run_impact(
            _project_dir(args),
            selection=args.selection,
            direction=direction,
            identity=identity,
        )
    print(
        emit_impact(
            compiled,
            result,
            format=args.format,
            direction=direction,
        )
    )
    return 0


def _run_clean(args: argparse.Namespace) -> int:
    report, _compiled = run_clean(_project_dir(args))
    if args.format == "json":
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=False))
    else:
        if not report.findings:
            print("clean: no findings")
        for finding in report.findings:
            print(f"{finding.severity}: {finding.check_id}: {finding.message}")
    errors = [finding for finding in report.findings if finding.severity == "error"]
    return 1 if errors else 0


def _run_contract(args: argparse.Namespace) -> int:
    artifact = load_artifact_file(Path(args.artifact_path))
    enforce_graph(artifact, posture="strict")
    print(f"contract: valid ({args.artifact_path})")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    require_experimental("cm serve")
    from clearmetric.compiler.compile import build_graph
    from clearmetric.runtime.serve import serve_project

    root = _project_dir(args)
    built = build_graph(root)
    serve_project(
        artifact_path=Path(args.artifact_path),
        project_dir=root,
        identity=args.identity,
        rules_path=Path(built.project.policy.rules),
        host=args.host,
        port=args.port,
    )
    return 0


def _run_query(args: argparse.Namespace) -> int:
    require_experimental("cm query")
    from clearmetric.runtime import execute_project_query

    identity = require_query_identity(args.identity)
    root = _project_dir(args)
    compiled = run_compile(root)
    rows = execute_project_query(
        compiled.artifact,
        identity=identity,
        rules_path=compiled.project.policy.rules,
        query_selection=args.query_id,
        project_dir=root,
    )
    print(json.dumps(rows, indent=2, sort_keys=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_root_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _run_init(args)
        if args.command == "connect":
            return _run_connect(args)
        if args.command == "scan":
            return _run_scan(args)
        if args.command == "compile":
            return _run_compile(args)
        if args.command == "impact":
            return _run_impact(args)
        if args.command == "clean":
            return _run_clean(args)
        if args.command == "contract":
            return _run_contract(args)
        if args.command == "query":
            return _run_query(args)
        if args.command == "serve":
            return _run_serve(args)
    except ClearMetricError as exc:
        print(f"cm error: {exc}", file=sys.stderr)
        return 1

    print(f"cm: unknown command {args.command!r}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser (for docs generation and tests)."""
    return _build_root_parser()


__all__ = ["build_parser", "main"]
