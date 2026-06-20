# Contributing

Thanks for contributing to `CatalogKit`.

## Setup

Create a virtual environment and install the packages you need in editable mode:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e packages/catalogkit-core
python -m pip install -e "packages/catalogkit-query[dev,release]"
```

The `catalogkit` meta-package is dependency metadata only. Do not create a
`catalogkit/__init__.py` file or any other namespace-root Python module.

## Run The Checks

Run the local test suite:

```bash
pytest -v
```

Build and validate packages before release-facing changes:

```bash
python -m build packages/catalogkit-core
python -m build packages/catalogkit-query
python -m build packages/catalogkit
python -m twine check packages/catalogkit-core/dist/*
python -m twine check packages/catalogkit-query/dist/*
python -m twine check packages/catalogkit/dist/*
```

## Release Workflow

PyPI Trusted Publishing should point at `.github/workflows/publish.yml`.

- workflow file: `publish.yml`
- GitHub Actions environment: `pypi`
- trigger: package tag push or manual `workflow_dispatch`
- supported package names: `catalogkit-core`, `catalogkit-query`, and `catalogkit`

Manual migration steps that stay outside the codebase:

1. Publish `catalogkit-core`.
2. Publish `catalogkit-query`.
3. Publish `catalogkit`.
4. Publish one final `querymap` release as a deprecated pointer package to
   `catalogkit-query`.
5. Keep `querymap` available for a transition period instead of yanking it
   immediately.

## Contribution Rules

- Keep the public contract narrow and explicit.
- Reuse the centralized public API instead of introducing parallel entrypoints.
- Remove duplicate, dead, or fallback behavior instead of preserving it behind compatibility layers.
- Fail loudly on unsupported input rather than returning partial or ambiguous output.
- Keep docs, tests, and code aligned in the same change.
- Add tests only where they materially protect the public contract or release path.
- Keep shared contract logic in `catalogkit-core`; do not recreate ID or merge rules in tool packages.

## Scope Guardrails

This OSS monorepo is intentionally limited. Do not add:

- enterprise adapters
- proprietary comparison logic
- auth, RBAC, or RLS behavior
- route handlers or API wiring
- warehouse-connected enrichment paths

## Pull Requests

Keep pull requests small, direct, and honest about scope. If a change expands the
public contract, update `README.md`,
`packages/catalogkit-core/docs/contract.md`, and the relevant tests in the same
pull request.
