# Development

## Editable Install

```bash
python -m pip install -e "packages/clearmetric-core[dev,release]"
```

## Tests

Run the full suite from the repository root:

```bash
python -m pytest -v packages/clearmetric-core/tests tests/
```

Wedge end-to-end (CLI subprocess + compiler API parity):

```bash
python -m pytest -v packages/clearmetric-core/tests/wedge/
```

Run repository boundary checks:

```bash
python -m pytest -v tests/test_repository_boundaries.py
python -m pytest -v tests/test_backbone_lab_boundaries.py
python -m pytest -v tests/test_cross_package_artifacts.py
python -m pytest -v tests/test_module_independence.py
```

Backbone lab (experimental; requires `CM_EXPERIMENTAL=1` in subprocess tests):

```bash
python -m pytest -v \
  packages/clearmetric-core/tests/test_lab_cli.py \
  packages/clearmetric-core/tests/test_mvp_demo.py \
  packages/clearmetric-core/tests/test_example_backbone_lab_e2e.py \
  packages/clearmetric-core/tests/compiler/test_compile_contracts.py \
  packages/clearmetric-core/tests/policy/test_gate.py \
  packages/clearmetric-core/tests/runtime/
```

Local backbone lab smoke:

```bash
export CM_EXPERIMENTAL=1
cd examples/backbone-lab
cm compile --format consumer-catalog --identity analyst
cm query --identity analyst query:executive_revenue
```

Lineage trust gate:

```bash
python -m pytest -q packages/clearmetric-core/tests/lineage
python -m pytest -q tests/test_repository_boundaries.py
PYTHONPATH=packages/clearmetric-core/src \
  python packages/clearmetric-core/scripts/sweep_lineage_coverage.py
```

Hand-traced external corpus validation is private (gitignored `corpus/` and `harness/`). See
`synthetic case layout in corpus.example/cases/example/`.

Static checks (same as CI):

```bash
python -m pip install ruff pyright
python -m ruff check .
python -m ruff format --check .
pyright
```

## Local wedge smoke

```bash
cd examples/lineage-demo
cm scan
cm compile --format json > graph.json
cm compile --format catalog > catalog.json
cm impact orders.amount --upstream
cm clean
cm contract graph.json
```

## Docs site

Regenerate the CLI reference when `clearmetric.cli` help text changes:

```bash
python -m pip install -e "packages/clearmetric-core[docs,runtime]"
python scripts/generate_wiki.py
mkdocs serve
```

CI checks `python scripts/generate_wiki.py --check` and `mkdocs build --strict`.

## Builds

```bash
python -m build packages/clearmetric-core
python -m twine check packages/clearmetric-core/dist/*
```

Smoke test the installed wheel:

```bash
python -m venv .pkgsmoke
source .pkgsmoke/bin/activate
python -m pip install packages/clearmetric-core/dist/*.whl
cm --version
python -c "import clearmetric.core; import clearmetric.lineage; import clearmetric.query; import clearmetric.powerbi"
```

## Release

1. Bump `clearmetric.core._version.__version__` and `CHANGELOG.md`.
2. Commit and push to `main`.
3. Tag and push — CI publishes to PyPI on tag `clearmetric-core-v*`:

```bash
git tag clearmetric-core-v0.3.0
git push origin clearmetric-core-v0.3.0
```
