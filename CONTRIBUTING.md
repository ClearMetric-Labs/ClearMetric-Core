# Contributing

Thanks for contributing to ClearMetric Core.

## Contributor License Agreement

ClearMetric Core uses a [Contributor License Agreement (CLA)](CLA.md) so ClearMetric can safely maintain, distribute, and evolve the project over time.

By opening a pull request, you agree that your contribution is submitted under the [ClearMetric LLC Individual Contributor License Agreement](CLA.md). Pull requests may not be merged until the CLA check passes.

When you open your first pull request, a bot will comment with signing instructions. Sign by posting this comment on the pull request:

```text
I have read the CLA Document and I hereby sign the CLA
```

You only need to sign once. If the check does not update after signing, comment `recheck` on the pull request.

## Protected Repository Files

Changes to `CLA.md`, `LICENSE`, `CONTRIBUTING.md`, `.github/CODEOWNERS`, and
files under `.github/workflows/` are treated as protected repository policy
changes.

If your pull request changes any of those files, a maintainer must review the
change and apply either the `maintainer-approved` or `legal-approved` label
before the protected-files check will pass.

## Setup

Create a virtual environment and install the package in editable mode:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "packages/clearmetric-core[dev,release]"
```

## Preview ClearMetric Core Docs locally

```bash
python -m pip install -e "packages/clearmetric-core[docs,runtime]"
python scripts/generate_wiki.py
mkdocs serve
```

Published site: [clearmetric-labs.github.io/ClearMetric-Core](https://clearmetric-labs.github.io/ClearMetric-Core/)

## Run The Checks

Run the centralized repo quality checks before opening a pull request:

```bash
python -m pip install ruff pyright
python -m ruff check .
python -m ruff format --check .
pyright
if rg -i 'catalogkit' --glob '!.git' --glob '!CHANGELOG.md' --glob '!CONTRIBUTING.md' --glob '!.github/workflows/ci.yml' .; then echo "legacy name references remain"; exit 1; fi
```

Use Ruff to apply the repo's shared formatting and import-order rules:

```bash
python -m ruff check . --fix
python -m ruff format .
```

Run the local test suite:

```bash
python -m pytest -v packages/clearmetric-core/tests tests/
```

Lineage corpus invariants require the dev extra (PyYAML for ground-truth probes):

```bash
python -m pytest -v \
  packages/clearmetric-core/tests/lineage/test_corpus_invariants.py \
  packages/clearmetric-core/tests/lineage/test_ground_truth.py
PYTHONPATH=packages/clearmetric-core \
  python packages/clearmetric-core/scripts/sweep_lineage_coverage.py
```

Build and validate the package before release-facing changes:

```bash
python -m build packages/clearmetric-core
python -m twine check packages/clearmetric-core/dist/*
```

## Release Workflow

ClearMetric Core publishes as a **single PyPI package**: `clearmetric-core`. Tag and
release once; all `clearmetric.*` modules move together.

PyPI Trusted Publishing should point at `.github/workflows/publish.yml`.

- workflow file: `publish.yml`
- GitHub Actions environment: `pypi`
- trigger: `clearmetric-core-v*` tag push or manual `workflow_dispatch`
- package tag versions must match the package source version exactly

Publish by tagging:

```bash
git tag clearmetric-core-v0.3.0
git push origin clearmetric-core-v0.3.0
```

While ClearMetric Core is in 0.x:

- breaking changes bump the package minor version, for example `0.1.0` to
  `0.2.0`
- non-breaking additions bump the package patch version, for example `0.1.0` to
  `0.1.1`
- the artifact schema `version` field bumps only for a breaking schema change
  and is decoupled from package versions
- 1.0 is declared only when the artifact contract is considered stable

## Contribution Rules

- Keep the public contract narrow and explicit.
- Reuse the centralized public API instead of introducing parallel entrypoints.
- Remove duplicate, dead, or fallback behavior instead of preserving it behind compatibility layers.
- Fail loudly on unsupported input rather than returning partial or ambiguous output.
- Keep docs, tests, and code aligned in the same change.
- Add tests only where they materially protect the public contract or release path.
- Keep shared contract logic in `clearmetric.core`; do not recreate ID or merge rules in tool modules.

## Scope Guardrails

This OSS monorepo is intentionally limited. Do not add:

- enterprise adapters
- proprietary comparison logic
- auth, RBAC, or RLS behavior
- route handlers or API wiring
- live warehouse connectors, query execution, or credential-based metadata sync (v1 uses local INFORMATION_SCHEMA JSON exports)

New modules must follow [docs/modules_instruction.md](docs/modules_instruction.md).

## Pull Requests

Keep pull requests small, direct, and honest about scope. If a change expands the
public contract, update `README.md`,
`docs/reference/contract.md`, and the relevant tests in the same
pull request.
