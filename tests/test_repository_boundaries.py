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
        "clearmetric.compiler",
        "clearmetric.projection",
        "clearmetric.graph",
        "clearmetric.policy",
    },
    "cleaner": {"clearmetric.core", "clearmetric.graph"},
    "policy": {"clearmetric.core", "clearmetric.policy"},
    "projection": {"clearmetric.core", "clearmetric.policy"},
    "compiler": {
        "clearmetric.core",
        "clearmetric.adapters",
        "clearmetric.cleaner",
        "clearmetric.policy",
        "clearmetric.graph",
        "clearmetric.query",
    },
    "runtime": {
        "clearmetric.core",
        "clearmetric.policy",
        "clearmetric.runtime",
    },
    "cli": {
        "clearmetric.core",
        "clearmetric.cli",
        "clearmetric.compiler",
        "clearmetric.emitters",
        "clearmetric.policy",
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
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    module_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            module_imports.append(node.module)
    assert "clearmetric.lineage" not in module_imports
    assert "clearmetric.powerbi" not in module_imports
    assert not any(name == "clearmetric.runtime" for name in module_imports)


def test_runtime_module_exists_for_lab():
    assert (SRC_ROOT / "runtime" / "__init__.py").is_file()


def test_projection_does_not_import_evaluate_node():
    project_path = MODULE_ROOTS["projection"] / "project.py"
    source = project_path.read_text(encoding="utf-8")
    assert "evaluate_node" not in source
    assert "gate" in source


def test_lineage_build_does_not_define_traversal():
    build_path = MODULE_ROOTS["lineage"] / "build.py"
    source = build_path.read_text(encoding="utf-8")
    assert "def trace_upstream_from_artifact" not in source
    assert "def trace_downstream_from_artifact" not in source
    assert "def build_openlineage_export_from_artifact" not in source


def test_lineage_public_api_is_build_only():
    init_path = MODULE_ROOTS["lineage"] / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    for banned in (
        "trace_upstream_from_artifact",
        "trace_downstream_from_artifact",
        "TraversalResult",
        "build_openlineage",
    ):
        assert banned not in source


def test_lineage_render_does_not_define_traversal():
    text_path = MODULE_ROOTS["lineage"] / "render" / "text.py"
    source = text_path.read_text(encoding="utf-8")
    assert "def render_traversal_tree" not in source
    assert not (MODULE_ROOTS["lineage"] / "render" / "mermaid.py").exists()


def test_emitters_do_not_import_lineage():
    emitters_root = MODULE_ROOTS["emitters"]
    violations: list[str] = []
    for path in emitters_root.rglob("*.py"):
        if _is_ignored_package_path(path):
            continue
        source = path.read_text(encoding="utf-8")
        if "clearmetric.lineage" in source:
            violations.append(f"{path}: references clearmetric.lineage")
    assert violations == []


def test_emitters_do_not_import_evaluate_node():
    emitters_root = MODULE_ROOTS["emitters"]
    violations: list[str] = []
    for path in emitters_root.rglob("*.py"):
        if _is_ignored_package_path(path):
            continue
        if path.name == "registry.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "evaluate_node" in source:
            violations.append(f"{path}: references evaluate_node")
        if "from clearmetric.policy import gated_context" in source:
            violations.append(f"{path}: imports gated_context")
        if "gated_context(" in source:
            violations.append(f"{path}: calls gated_context")
    assert violations == []


def test_emitters_registry_may_import_policy_context():
    registry_path = MODULE_ROOTS["emitters"] / "registry.py"
    source = registry_path.read_text(encoding="utf-8")
    assert "gated_context" in source
    assert "evaluate_node" not in source


def test_cli_normal_compile_choices_are_wedge_only():
    from clearmetric.cli.experimental import (
        compile_format_choices,
        is_experimental_enabled,
    )

    if is_experimental_enabled():
        return
    assert compile_format_choices() == ("json", "text", "openlineage", "catalog")


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


CONSUMERS_ROOT = REPO_ROOT / "examples" / "consumers"
BUILD_BUNDLE = REPO_ROOT / "scripts" / "consumers" / "build_bundle.py"
_BANNED_BUILD_BUNDLE_IMPORTS = (
    "clearmetric.emitters",
    "clearmetric.projection",
    "clearmetric.policy",
    "clearmetric.runtime",
)
_BANNED_CONSUMER_STRINGS = (
    "policy.gate",
    "apply_policy",
    "require_allow",
    "CM_EXPERIMENTAL",
)


def test_consumer_viewers_do_not_embed_policy_or_python():
    violations: list[str] = []
    for path in CONSUMERS_ROOT.rglob("*"):
        if path.suffix not in {".mjs", ".html", ".css"}:
            continue
        if "bundles/" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "clearmetric." in text
            or "from clearmetric" in text
            or "import clearmetric" in text
        ):
            violations.append(f"{path}: imports clearmetric Python modules")
        for banned in _BANNED_CONSUMER_STRINGS:
            if banned in text:
                violations.append(f"{path}: contains {banned!r}")
    assert violations == []


def test_build_bundle_import_boundary():
    tree = ast.parse(
        BUILD_BUNDLE.read_text(encoding="utf-8"), filename=str(BUILD_BUNDLE)
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for banned in _BANNED_BUILD_BUNDLE_IMPORTS:
                    if alias.name == banned or alias.name.startswith(f"{banned}."):
                        violations.append(f"import {alias.name}")
        if isinstance(node, ast.ImportFrom) and node.module:
            for banned in _BANNED_BUILD_BUNDLE_IMPORTS:
                if node.module == banned or node.module.startswith(f"{banned}."):
                    violations.append(f"from {node.module} import ...")
            if node.module.startswith("clearmetric.graph"):
                violations.append(f"from {node.module} import ...")
    assert violations == []
