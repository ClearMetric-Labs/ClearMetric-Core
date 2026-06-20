# Development

## Editable Installs

Install the shared core first, then any tool package that depends on it:

```bash
python -m pip install -e packages/catalogkit-core
python -m pip install -e "packages/catalogkit-query[dev,release]"
```

The `catalogkit` meta-package ships no Python package files. It exists only to
install the current CatalogKit distributions together.

## Tests

Run the full suite from the repository root:

```bash
python -m pytest -v
```

Run package-focused tests:

```bash
python -m pytest -v packages/catalogkit-core/tests
python -m pytest -v packages/catalogkit-query/tests
python -m pytest -v tests/test_repository_boundaries.py
```

## Builds

Build packages independently:

```bash
python -m build packages/catalogkit-core
python -m build packages/catalogkit-query
python -m build packages/catalogkit
```

Smoke test the namespace package install path with built wheels:

```bash
python -m venv .pkgsmoke
source .pkgsmoke/bin/activate
python -m pip install packages/catalogkit-core/dist/*.whl
python -m pip install packages/catalogkit-query/dist/*.whl
python -m pip install --no-deps packages/catalogkit/dist/*.whl
python -c "import catalogkit.core; import catalogkit.query"
```
