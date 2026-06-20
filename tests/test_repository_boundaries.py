from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_ROOT = REPO_ROOT / "packages"
PACKAGE_SOURCE_ROOTS = {
    "catalogkit-core": PACKAGES_ROOT / "catalogkit-core" / "src" / "catalogkit" / "core",
    "catalogkit-query": PACKAGES_ROOT / "catalogkit-query" / "src" / "catalogkit" / "query",
}
SHARED_CLASS_NAMES = {"Node", "Edge", "Evidence", "Warning"}


def test_tool_packages_only_depend_on_catalogkit_core_and_themselves():
    violations: list[str] = []

    for package_name, package_root in PACKAGE_SOURCE_ROOTS.items():
        allowed_modules = {"catalogkit.core"}
        if package_name == "catalogkit-query":
            allowed_modules.add("catalogkit.query")

        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("catalogkit.") and not _is_allowed_module(
                            alias.name,
                            allowed_modules,
                        ):
                            violations.append(f"{path}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    if node.module.startswith("catalogkit.") and not _is_allowed_module(
                        node.module,
                        allowed_modules,
                    ):
                        violations.append(f"{path}: from {node.module} import ...")

    assert violations == []


def test_shared_model_class_names_exist_only_in_catalogkit_core():
    violations: list[str] = []
    core_models_path = PACKAGES_ROOT / "catalogkit-core" / "src" / "catalogkit" / "core" / "models.py"

    for path in PACKAGES_ROOT.rglob("*.py"):
        if path == core_models_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in SHARED_CLASS_NAMES:
                violations.append(f"{path}: class {node.name}")

    assert violations == []


def test_no_namespace_root_init_file_exists():
    violations = sorted(str(path.relative_to(REPO_ROOT)) for path in PACKAGES_ROOT.rglob("catalogkit/__init__.py"))
    assert violations == []


def _is_allowed_module(module_name: str, allowed_modules: set[str]) -> bool:
    return any(module_name == allowed or module_name.startswith(f"{allowed}.") for allowed in allowed_modules)
