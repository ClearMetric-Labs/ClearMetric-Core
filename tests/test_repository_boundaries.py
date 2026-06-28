from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "clearmetric-core"
SRC_ROOT = PACKAGE_ROOT / "src" / "clearmetric"
MODULE_ROOTS = {
    "core": SRC_ROOT / "core",
    "graph": SRC_ROOT / "graph",
    "query": SRC_ROOT / "query",
    "lineage": SRC_ROOT / "lineage",
    "powerbi": SRC_ROOT / "powerbi",
    "adapters": SRC_ROOT / "adapters",
    "emitters": SRC_ROOT / "emitters",
    "compiler": SRC_ROOT / "compiler",
    "cleaner": SRC_ROOT / "cleaner",
    "policy": SRC_ROOT / "policy",
    "projection": SRC_ROOT / "projection",
    "runtime": SRC_ROOT / "runtime",
    "cli": SRC_ROOT / "cli",
}
ALLOWED_MODULES_BY_SUBPACKAGE = {
    "core": {"clearmetric.core", "clearmetric.policy"},
    "graph": {"clearmetric.core", "clearmetric.graph"},
    "query": {"clearmetric.core", "clearmetric.query"},
    "lineage": {"clearmetric.core", "clearmetric.lineage", "clearmetric.graph"},
    "powerbi": {"clearmetric.core", "clearmetric.powerbi"},
    "adapters": {"clearmetric.core", "clearmetric.lineage"},
    "emitters": {
        "clearmetric.core",
        "clearmetric.lineage",
        "clearmetric.compiler",
        "clearmetric.projection",
        "clearmetric.policy",
        "clearmetric.graph",
    },
    "cleaner": {"clearmetric.core", "clearmetric.graph"},
    "policy": {"clearmetric.core", "clearmetric.policy", "clearmetric.projection"},
    "projection": {"clearmetric.core", "clearmetric.policy"},
    "runtime": {"clearmetric.core", "clearmetric.runtime"},
    "compiler": {
        "clearmetric.core",
        "clearmetric.adapters",
        "clearmetric.cleaner",
        "clearmetric.policy",
        "clearmetric.projection",
        "clearmetric.lineage",
        "clearmetric.graph",
        "clearmetric.query",
    },
    "cli": {
        "clearmetric.core",
        "clearmetric.cli",
        "clearmetric.compiler",
        "clearmetric.emitters",
        "clearmetric.cleaner",
        "clearmetric.runtime",
    },
}
SHARED_CLASS_NAMES = {"Node", "Edge", "Evidence", "Warning"}
PROPRIETARY_IMPORT_PREFIXES = (
    "apps",
    "auth",
    "clearmetric_cloud",
    "config",
    "database",
    "models",
    "services",
    "shared_config",
)
CORE_ONLY_INTEROP_SYMBOLS = {
    "apply_alias_map",
    "normalize_fqn_for_matching",
    "warehouse_table_fqn_candidates",
    "warehouse_table_fqn_candidates_from_name",
    "resolve_table_match",
    "load_table_alias_map",
}
IGNORED_PATH_PARTS = {
    ".pkgmeta",
    ".pkgsmoke",
    ".pkgtest",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


def _is_ignored_package_path(path: Path) -> bool:
    return any(
        part in IGNORED_PATH_PARTS or part.endswith(".egg-info") for part in path.parts
    )


def test_subpackages_only_import_allowed_clearmetric_modules():
    violations: list[str] = []

    for subpackage_name, package_root in MODULE_ROOTS.items():
        allowed_modules = ALLOWED_MODULES_BY_SUBPACKAGE[subpackage_name]

        for path in package_root.rglob("*.py"):
            if _is_ignored_package_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(
                            "clearmetric."
                        ) and not _is_allowed_module(alias.name, allowed_modules):
                            violations.append(f"{path}: import {alias.name}")
                elif (
                    isinstance(node, ast.ImportFrom) and node.module and node.level == 0
                ):
                    if node.module.startswith(
                        "clearmetric."
                    ) and not _is_allowed_module(node.module, allowed_modules):
                        violations.append(f"{path}: from {node.module} import ...")

    assert violations == []


def test_cli_does_not_import_lineage_or_powerbi():
    cli_path = MODULE_ROOTS["cli"] / "__init__.py"
    source = cli_path.read_text(encoding="utf-8")
    assert "clearmetric.lineage" not in source
    assert "clearmetric.powerbi" not in source


def test_lineage_does_not_import_compiler_or_adapters():
    lineage_root = MODULE_ROOTS["lineage"]
    banned = ("clearmetric.compiler", "clearmetric.adapters", "clearmetric.cli")
    violations: list[str] = []
    for path in lineage_root.rglob("*.py"):
        if _is_ignored_package_path(path):
            continue
        source = path.read_text(encoding="utf-8")
        for prefix in banned:
            if prefix in source:
                violations.append(f"{path}: references {prefix}")
    assert violations == []


def test_subpackages_do_not_import_enterprise_or_proprietary_prefixes():
    violations: list[str] = []

    for package_root in MODULE_ROOTS.values():
        for path in package_root.rglob("*.py"):
            if _is_ignored_package_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _has_banned_prefix(alias.name):
                            violations.append(f"{path}: import {alias.name}")
                elif (
                    isinstance(node, ast.ImportFrom) and node.module and node.level == 0
                ):
                    if _has_banned_prefix(node.module):
                        violations.append(f"{path}: from {node.module} import ...")

    assert violations == []


def test_shared_model_class_names_exist_only_in_core():
    violations: list[str] = []
    core_models_path = SRC_ROOT / "core" / "models.py"

    for path in SRC_ROOT.rglob("*.py"):
        if path == core_models_path or _is_ignored_package_path(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in SHARED_CLASS_NAMES:
                violations.append(f"{path}: class {node.name}")

    assert violations == []


def test_no_namespace_root_init_file_exists():
    violations = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in PACKAGE_ROOT.rglob("clearmetric/__init__.py")
    )
    assert violations == []


def test_cross_graph_interop_symbols_are_not_redefined_outside_core():
    violations: list[str] = []
    core_root = MODULE_ROOTS["core"]

    for subpackage_name, package_root in MODULE_ROOTS.items():
        if subpackage_name == "core":
            continue
        for path in package_root.rglob("*.py"):
            if _is_ignored_package_path(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name in CORE_ONLY_INTEROP_SYMBOLS
                ):
                    violations.append(f"{path}: def {node.name}")

    for path in core_root.rglob("*.py"):
        if _is_ignored_package_path(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name in CORE_ONLY_INTEROP_SYMBOLS
            ):
                if path.name not in {"interop.py", "aliases.py"}:
                    violations.append(f"{path}: def {node.name}")

    assert violations == []


def _is_allowed_module(module_name: str, allowed_modules: set[str]) -> bool:
    return any(
        module_name == allowed or module_name.startswith(f"{allowed}.")
        for allowed in allowed_modules
    )


def _has_banned_prefix(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in PROPRIETARY_IMPORT_PREFIXES
    )
