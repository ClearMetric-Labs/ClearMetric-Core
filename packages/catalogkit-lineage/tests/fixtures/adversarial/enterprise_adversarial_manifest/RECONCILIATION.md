# Edge-Count Reconciliation: independent oracle vs engine

## Independent oracle

`value_lineage_expected.yml` is **hand-authored**. Do not edit it to match
`expected.yaml` or engine output. Change it only when a human re-derives value
lineage from compiled SQL.

CI validates the live engine against this file via
`tests/test_value_lineage_oracle.py`. `expected.yaml` is a separate full-edge
snapshot for `test_adversarial.py`.

## Pinned breakdown (strict value-lineage)

| Model | Pre-strict engine | Hand oracle | Strict engine |
|---|---:|---:|---:|
| adv_01_select_star | 5 | 0 | 0 |
| adv_02_qualified_star | 6 | 1 | 1 |
| adv_03_union | 4 | 0 | 0 |
| adv_04–10, 13–15 | agree | agree | unchanged |
| adv_11_window | 3 | 3 | 3 |
| adv_12_quoted_idents | 3 | 0 | 0 |
| stg_* staging | 14 | 14 | 14 |

**70 before strict fix** = 56 adversarial + 14 staging (hybrid warn-and-enumerate on
adv_01, adv_02, adv_12).

**53 after strict fix** = 39 adversarial + 14 staging.

The entire gap from 70 → 53 is explained by three hybrid-defect models that emitted
`derives_from` edges while also warning (R6/R8), plus union positional merge (R7).

## adv_11 — proven in tests, not prose

The hand oracle expects 3 value edges (`id`, `user_id`, `running_total←amount`) and
warnings on window-only outputs. `test_value_lineage_oracle.py` asserts positive and
negative edge shapes so over-warning on direct pass-throughs would fail CI.

## Local dev script (not CI truth)

`scripts/reconcile_edge_counts.py` compares the hand oracle to per-model edge counts
read from `expected.yaml` (a snapshot of past engine output). It does **not** call
`build_lineage_map`. For live validation, run:

```bash
pytest tests/test_value_lineage_oracle.py
```
