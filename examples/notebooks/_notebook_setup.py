"""Colab-ready bootstrap for example notebooks."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

# Cold-start fallback only when `_paths` is not yet loadable (keep in sync with `_paths.GITHUB_RAW_BASE`).
_COLD_START_GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/ClearMetric-Labs/ClearMetric-Core/main"
)


# Keep in sync with `_paths.CACHED_NOTEBOOKS_DIR` (used before `_paths` is loadable).
_CACHED_NOTEBOOKS_DIR = (
    Path.home() / ".cache" / "clearmetric" / "github-main" / "examples" / "notebooks"
)


def _github_raw_base() -> str:
    paths = sys.modules.get("_paths")
    default = (
        paths.GITHUB_RAW_BASE if paths is not None else _COLD_START_GITHUB_RAW_BASE
    )
    return os.environ.get("CM_CLEARMETRIC_GITHUB_RAW_BASE", default)


def _find_local_helpers(start: Path | None = None) -> Path | None:
    start = start or Path.cwd()
    for root in (start, *start.parents):
        nested = root / "examples" / "notebooks"
        if (nested / "_paths.py").is_file():
            return nested
        if (root / "_paths.py").is_file() and (root / "_notebook_setup.py").is_file():
            return root
    return None


def _fetch_repo_file(repo_relative: str, dest: Path) -> None:
    if dest.is_file():
        return
    paths = sys.modules.get("_paths")
    if paths is not None:
        paths._fetch_github_file(repo_relative, dest)
        return
    url = f"{_github_raw_base()}/{repo_relative}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            dest.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise FileNotFoundError(
            f"Failed to download {url}. Check network access and branch path."
        ) from exc


def _resolve_setup_file(start: Path | None = None) -> Path:
    local = _find_local_helpers(start)
    if local is not None:
        return local / "_notebook_setup.py"
    paths = sys.modules.get("_paths")
    cache_dir = (
        paths.CACHED_NOTEBOOKS_DIR if paths is not None else _CACHED_NOTEBOOKS_DIR
    )
    setup_file = cache_dir / "_notebook_setup.py"
    if not setup_file.is_file():
        _fetch_repo_file("examples/notebooks/_notebook_setup.py", setup_file)
    return setup_file


def _exec_setup_module(setup_file: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_notebook_setup", setup_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {setup_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_paths_module(path_file: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_paths", path_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_paths"] = module
    spec.loader.exec_module(module)
    return module


def _pip_install(*, extras: str | None = None) -> None:
    if os.environ.get("CM_NOTEBOOK_SKIP_PIP"):
        return
    package = "clearmetric-core" if extras is None else f"clearmetric-core[{extras}]"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", package],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pip install {package} failed:\n{result.stderr or result.stdout}"
        )


def load_paths(start: Path | None = None) -> ModuleType:
    """Resolve notebook helpers locally or via GitHub cache; load `_paths` fresh."""
    local = _find_local_helpers(start)
    if local is not None:
        directory = local
    else:
        paths_file = _CACHED_NOTEBOOKS_DIR / "_paths.py"
        if not paths_file.is_file():
            _fetch_repo_file("examples/notebooks/_paths.py", paths_file)
        bootstrap_paths = _load_paths_module(paths_file)
        bootstrap_paths.sync_github_files(bootstrap_paths.NOTEBOOK_HELPER_FILES)
        directory = bootstrap_paths.CACHED_NOTEBOOKS_DIR
        if not (directory / "_paths.py").is_file():
            raise FileNotFoundError(
                f"Failed to fetch notebook helpers from {_github_raw_base()}"
            )

    path_str = str(directory)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
    return _load_paths_module(directory / "_paths.py")


def bootstrap(
    start: Path | None = None,
    *,
    pip_install: bool = True,
    extras: str | None = None,
) -> ModuleType:
    """Install clearmetric-core, resolve notebook helpers, load `_paths` fresh."""
    if pip_install:
        _pip_install(extras=extras)
    return load_paths(start)


def setup_notebook(*, extras: str | None = None) -> ModuleType:
    """Notebook cell-1 entry point (Colab or local clone)."""
    return bootstrap(pip_install=True, extras=extras)


def load_setup_module(start: Path | None = None) -> ModuleType:
    """Load this module from a clone or GitHub cache (used by notebook setup cells)."""
    return _exec_setup_module(_resolve_setup_file(start))


def format_notebook_setup_cell(setup_call: str, imports: str, body: str) -> str:
    """Build notebook cell 1 from the same bootstrap functions notebooks import at runtime."""
    import inspect

    paths = _load_paths_module(Path(__file__).resolve().parent / "_paths.py")
    if paths.GITHUB_RAW_BASE != _COLD_START_GITHUB_RAW_BASE:
        raise RuntimeError(
            "Sync _COLD_START_GITHUB_RAW_BASE with _paths.GITHUB_RAW_BASE"
        )
    cached_notebooks_dir = paths.CACHED_NOTEBOOKS_DIR
    helper_sources = (
        f"_COLD_START_GITHUB_RAW_BASE = {paths.GITHUB_RAW_BASE!r}\n\n"
        f"_CACHED_NOTEBOOKS_DIR = Path({str(cached_notebooks_dir)!r})\n\n",
        inspect.getsource(_github_raw_base),
        inspect.getsource(_fetch_repo_file),
        inspect.getsource(_find_local_helpers),
        inspect.getsource(_resolve_setup_file),
        inspect.getsource(_exec_setup_module),
    )
    return (
        "import importlib.util\n"
        "import os\n"
        "import sys\n"
        "import urllib.error\n"
        "import urllib.request\n"
        "from pathlib import Path\n"
        "from types import ModuleType\n\n"
        f"{''.join(helper_sources)}"
        f"_exec_setup_module(_resolve_setup_file()).setup_notebook({setup_call})\n"
        f"{imports}\n"
        f"{body}\n"
    )
