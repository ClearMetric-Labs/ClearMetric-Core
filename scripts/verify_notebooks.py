#!/usr/bin/env python3
"""Execute example notebook code cells in order (smoke test)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


def _import_setup_module(notebooks_dir: Path):
    setup_file = notebooks_dir / "_notebook_setup.py"
    spec = importlib.util.spec_from_file_location("_notebook_setup", setup_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {setup_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_setup_module(notebooks_dir)


def _run_notebook(path: Path, *, skip_substrings: tuple[str, ...] = ()) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    for index, cell in enumerate(payload.get("cells") or []):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source") or [])
        if not source.strip():
            continue
        if any(token in source for token in skip_substrings):
            continue
        try:
            exec(compile(source, f"{path.name}:cell_{index}", "exec"), namespace)
        except Exception as exc:
            raise RuntimeError(f"{path.name} cell {index} failed: {exc}") from exc


def _notebook_names(selected: list[str] | None) -> list[str]:
    if selected:
        return selected
    return [
        "01_public_wedge_lineage.ipynb",
        "02_compile_formats.ipynb",
        "03_impact_analysis.ipynb",
        "04_consumer_bundle.ipynb",
        "05_backbone_lab_experimental.ipynb",
    ]


def _skip_for(name: str) -> tuple[str, ...]:
    if name.startswith("04_"):
        return ("TemporaryDirectory", "skip regenerate:")
    if name.startswith("05_"):
        return ("execute_project_query",)
    return ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test example notebooks")
    parser.add_argument("notebooks", nargs="*", help="Notebook filenames to run")
    parser.add_argument(
        "--colab-sim",
        action="store_true",
        help="Run from a temp dir with no local clone (GitHub fetch path)",
    )
    parser.add_argument(
        "--skip-pip",
        action="store_true",
        help="Set CM_NOTEBOOK_SKIP_PIP=1 for faster local runs",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    notebooks_dir = repo_root / "examples" / "notebooks"

    if args.skip_pip:
        os.environ["CM_NOTEBOOK_SKIP_PIP"] = "1"

    if args.colab_sim:
        setup = _import_setup_module(notebooks_dir)
        paths = setup.load_paths(start=notebooks_dir)
        paths.seed_github_cache_from_repo(repo_root)
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            for name in _notebook_names(args.notebooks):
                path = notebooks_dir / name
                print(f"running {name} (colab-sim) ...", flush=True)
                _run_notebook(path, skip_substrings=_skip_for(name))
                print("  ok", flush=True)
        return 0

    sys.path.insert(0, str(notebooks_dir))
    for name in _notebook_names(args.notebooks):
        path = notebooks_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"running {name} ...", flush=True)
        _run_notebook(path, skip_substrings=_skip_for(name))
        print("  ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
