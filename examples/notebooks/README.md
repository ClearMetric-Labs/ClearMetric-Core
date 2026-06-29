# ClearMetric example notebooks

Guided curriculum for the public wedge. Each notebook is **self-runnable** in **Google Colab** or locally: the setup cell runs `pip install clearmetric-core` and pulls fixtures from the public GitHub repo when you are not in a clone.

Repo: [ClearMetric-Labs/ClearMetric-Core](https://github.com/ClearMetric-Labs/ClearMetric-Core/tree/main/examples/notebooks)

## Colab quick start

1. Open any notebook in Colab (upload or open from GitHub).
2. Run **cell 1** (setup). It installs `clearmetric-core` and resolves paths.
3. Run all cells top to bottom.

Contributors with a full clone skip GitHub fetch automatically — local paths are preferred.

Override the fetch branch with `CM_CLEARMETRIC_GITHUB_RAW_BASE` (raw URL prefix, no trailing slash).

```bash
# Local alternative
python -m pip install -e "packages/clearmetric-core[dev,runtime]"
jupyter lab examples/notebooks/
```

Smoke-test: `CM_NOTEBOOK_SKIP_PIP=1 python scripts/verify_notebooks.py --skip-pip`

Colab-simulation (no local clone): `CM_NOTEBOOK_SKIP_PIP=1 python scripts/verify_notebooks.py --colab-sim --skip-pip`

The verify script seeds the GitHub mirror cache from your checkout before changing cwd, so PR CI can exercise the Colab path without waiting for `main` to update.

Maintainers regenerate notebook cells from `scripts/generate_notebooks.py` (setup cell source comes from `_notebook_setup.format_notebook_setup_cell`).

## Curriculum

| # | Notebook | You learn |
|---|----------|-----------|
| 01 | [01_public_wedge_lineage.ipynb](01_public_wedge_lineage.ipynb) | Raw inputs → standardize → compile → impact preview |
| 02 | [02_compile_formats.ipynb](02_compile_formats.ipynb) | `json`, `catalog`, `openlineage`, `text` emit formats |
| 03 | [03_impact_analysis.ipynb](03_impact_analysis.ipynb) | Upstream/downstream semantics, sink vs mid-pipeline, renderers |
| 04 | [04_consumer_bundle.ipynb](04_consumer_bundle.ipynb) | Consumer bundles, manifest contract, corpus checks |
| 05 | [05_backbone_lab_experimental.ipynb](05_backbone_lab_experimental.ipynb) | **Experimental** lab formats + policy-gated impact |

## Fixture source

Notebooks **01–03** use [`lineage-demo`](../lineage-demo) (Shopify-scale warehouse + SQL pipeline).

Notebook **04** uses committed bundles under [`examples/consumers/bundles/`](../consumers/bundles/).

Notebook **05** uses [`examples/backbone-lab`](../backbone-lab) (requires `[runtime]` for query execution).

## Impact ground truth (01–03)

| Selection | Direction | Related columns |
|-----------|-----------|-----------------|
| `customers_report.customer_lifetime_value` | upstream | 3 |
| `orders_base.amount` | downstream | 2 |

Downstream from the sink column (`customers_report.customer_lifetime_value`) is **empty by design**.

## Related docs

- [Five-minute demo](https://clearmetric-labs.github.io/ClearMetric-Core/getting-started/five-minute-demo/) — docs site walkthrough
- [examples/consumers/README.md](../consumers/README.md) — viewer apps and bundle security model
- [examples/backbone-lab/README.md](../backbone-lab/README.md) — experimental CLI lab commands
