# Local resolver case layout (public template)

The synthetic example under `cases/example/` shows the file shape for a hand-traced lineage
case. Real cases with proprietary expectations live in a private gitignored corpus.

## Required case files

Each case under `corpus/cases/<case_id>/` must contain:

- `meta.yaml` — dialect, `case_kind` (`lineage_truth` or `behavior_spec`), traced_by, traced_at
- `expected.yaml` — hand-verified derives_from edges and warnings
- `must_not_edges.yaml` — forbidden edges that catch over-production
- `notes.md` — SQL trace notes; required when changing expectations
- Either `sql/model.sql` + `schema.json` **or** a trimmed `manifest.json` slice for ref-chain projects

## Discipline

1. Trace SQL by hand
2. Write `expected.yaml` and `must_not_edges.yaml`
3. Run the resolver and diff
4. Never edit expectations to match resolver output without updating `notes.md`

Committed CI coverage uses `packages/clearmetric-core/tests/fixtures/lineage/seed/` and
adversarial fixtures — not this template directory.

## Private harness remeasurement

Long Tuva discrepancy remeasurement uses a **build-once** scope union (not per-target
rebuilds). Run outside IDE timeouts (e.g. `tmux`):

```bash
export CLEARMETRIC_LINEAGE_CACHE_DIR=corpus/reports/_lineage_build_cache
cd packages/clearmetric-core
uv run python scripts/corpus_external.py residual-overlap --repo tuva --force
uv run python scripts/corpus_external.py residual-overlap --repo tuva --evaluate-checkpoints
```

Stale lineage cache or checkpoints raise errors; pass `--force` after engine or baseline
changes. Never commit `corpus/` — only `corpus.example/` is public.
