# Check lineage yourself

Do not trust impact output blindly — spot-check columns you know on your project.

## 1. Compile and save the graph

```bash
cm scan
cm compile --format json > graph.json 2> compile-warnings.log
```

Read `compile-warnings.log`. Warnings mean the resolver flagged uncertainty on those subjects.

## 2. Pick a column you know

Choose a base column and a downstream metric or report column whose lineage you can explain from
SQL or dbt compiled models.

```bash
cm impact YOUR_BASE_COLUMN --downstream --format json
cm impact YOUR_REPORT_COLUMN --upstream --format json
```

Selection forms: `model.column`, `column:model.column`, or other forms accepted by `cm impact --help`.

Compare `related_ids` to your mental model. Missing columns often correlate with warnings on
compile or impact output.

## 3. Visual check (optional)

```bash
cm impact YOUR_COLUMN --downstream --format mermaid
```

Paste into a Mermaid viewer for a quick graph sketch.

## 4. CI hygiene

```bash
cm clean
cm contract graph.json
```

`clean` reports structural and posture findings; exit code 1 only on severity **error**.
Binding warnings are expected on some projects until metadata aligns with model names.

## 5. Report gaps

If expected lineage is missing and you believe the SQL is unambiguous, file an issue using
[Help test on real projects](help-test.md).

SQL pattern reference: [SQL support and limits](sql-limits.md).
