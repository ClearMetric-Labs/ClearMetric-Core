# ClearMetric

**The analytics backbone. One canonical, queryable, governed graph of your entire analytics
layer — compiled from the SQL, dbt, and warehouse metadata you already have — that you can
build anything on top of.**

---

## Overview

Analytics today is scattered across tools that don't agree with each other. What a metric
*means* lives in one place, where its data *comes from* lives in another, who's *allowed to
see it* lives in a third, and what the dashboard *shows* lives in a fourth. They drift. Nobody
can answer "what breaks if I change this column" without grepping five systems and hoping.

ClearMetric compiles the layer *above* your data warehouse into **one canonical graph** —
the analytics backbone. It derives that graph from artifacts you already produce (your dbt
project, your raw SQL, your warehouse's INFORMATION_SCHEMA metadata), resolves one canonical
identity for every table, column, and metric, and stamps every fact with where it came from
and how confident it is.

**That backbone is the product.** Everything else is something you build *on* it. Lineage is
a view of the graph. A catalog is a view of the graph. Impact analysis is a traversal of the
graph. A BI frontend binds to contracts over the graph. An AI agent reads a governed slice
of the graph. Access policy filters the graph. Documentation is emitted from the graph. None
of these are separate products with their own private source of truth that drifts — they are
all views, traversals, and emissions of **one graph that cannot disagree with itself.**

It is **not** a data warehouse and **not** a transformation tool (use dbt or existing OSS
for that). It is **not** a BI tool or a renderer — it produces the graph, the contracts, and
the results; you bring your own frontend, your own AI, your own presentation. ClearMetric
owns the connective backbone between the model and the consumer, so your analytics is
constrained by your infrastructure, never trapped inside someone's software.

## What I'm trying to achieve

Every BI and analytics tool I've used traps the semantics inside itself. Your metric
definitions, your lineage, your access rules live in Power BI's guts or Tableau's workbook
XML or a vendor's cloud, and they die there — you can't query them, version them, diff them,
or take them with you. The result is dashboard graveyards nobody will delete because nobody
knows what's load-bearing, metric definitions copy-pasted across forty workbooks that slowly
drift apart, and "governance" that means a six-month rollout of shelfware.

The goal of ClearMetric is to invert that: make the analytics backbone an **open, queryable
graph** derived from infrastructure you already have, so that anything — a dashboard, an
agent, a catalog, a governance check, a doc site — is built *on* the backbone instead of
re-implementing its own private, drifting copy of the truth. Specifically:

- **the structure writes itself** — lineage, dependencies, types, and impact are *derived*
  from your SQL and warehouse metadata, not re-typed by hand;
- **you only author what can't be derived** — what a metric means, who owns it, what's
  sensitive, who may see it — and the backbone is honest about which is which;
- **best practices are the default path**, not an exam — the safe, governed structure is what
  you get for free, and the backbone is opinionated about *structure and integrity* while
  staying out of the way on *content and judgment*;
- **you can build anything on top** — new sources, new outputs, new checks, new policy, new
  per-role views, new consumers nobody's thought of yet — all additive, because the core
  operates on one standardized graph and knows nothing about where inputs come from or where
  outputs go;
- **nothing is trapped** — the graph and every artifact emit in open formats you own and can
  leave with, and the engine itself is open source.

## What you build on it

The backbone is one graph; these are consumers of it, not separate products:

- **Lineage & impact** — column-level lineage and "what breaks if I change this" as a
  traversal of the graph.
- **Catalog & metadata** — a browsable view of every table, column, model, and its
  provenance.
- **Schema-drift detection** — the graph validated against warehouse reality, surfacing
  where definitions and data have diverged.
- **Governed query contracts** — bindable contracts a BI frontend (React, Streamlit,
  Evidence, an internal app) builds on, so you own the presentation and the backbone owns the
  correctness.
- **AI-agent context** — a governed, policy-filtered slice of the graph, serialized for an
  LLM you bring (your model, your key).
- **Access governance** — RBAC, RLS, masking, and AI-permission as policy over graph nodes,
  compiled down to real warehouse policies.
- **Automated documentation** — docs emitted from the graph, structurally always-fresh
  because they're derived, with authored meaning layered on.

Each is a view, traversal, projection, or emission of the same backbone. Adding a new one is
additive — it reads the graph it never owns. That open-endedness *is* the product: the
backbone is the durable thing; what you build on it is unbounded.

---

## What it is (precisely)

A code-first analytics control plane that compiles code-defined metrics, queries,
lineage, metadata, and policy intent into one canonical graph of queryable nodes and
bindable contracts that BI frontends, catalogs, and AI agents safely build on.

It is **not** a transformation tool (leverage dbt / existing OSS). It is **not** a renderer
(it supplies the contract, query, and result; the frontend renders). It owns the layer
between the model and the consumer.

Four phrases carry the architecture:

- **Open-source core, paid managed history.**
- **Logical IDs with physical bindings.**
- **Contracts, not dashboards.**
- **Derived metadata with confidence, not magic.**

---

## The central design tension this document resolves

Two requirements that sound opposed:

- **Opinionated** — force best practice, no redundancy, no duplicates, no ungoverned
  sprawl, so an inexperienced user cannot unknowingly wreck their infra.
- **Flexible** — users define their own permissions, metrics, checks, node and projection
  types; the tool dictates almost no content.

The resolution is a single principle that runs through the whole architecture:

> **The system is rigid about STRUCTURE and lenient about CONTENT.**
> It enforces *how things must be shaped and related* (identity, provenance,
> completeness, non-duplication) and stays out of *what things mean and who may see
> them* (metric logic, classifications, policy rules, personas, custom checks).

Opinionation lives in the **shape and integrity of the graph**. Flexibility lives in the
**content the user pours into that shape** — including the checks they run against it.
They do not conflict because they operate on different layers: you cannot violate the
structure, but within the structure you define everything.

---

## The security floor — extensible, but never into a hole

The "rigid structure / flexible content" principle has a dangerous gap if left alone:
**security is content**, so unrestricted flexibility would let a user — especially an
inexperienced one — extend the system straight into an insecure state. They could expose
PII to an AI agent, wipe the deny-by-default seed, disable a security check, or carry
sensitive data in an unclassified aspect. That is exactly the "unknowingly wreck their
infra" failure this product exists to prevent.

So security is **not** ordinary content. A small set of **security invariants lives in the
structural tier** — the same impossible-to-violate floor as canonical identity. Posture
cannot lower them. User checks cannot disable them. Wiping the policy seeds cannot remove
them. Everything *above* the floor stays fully extensible; the floor itself is
non-negotiable underneath.

### The security floor (structural — always on, every posture)

```
1. NO UNCLASSIFIED EXPOSURE
   A node reachable by an AI-permission or export rule MUST carry a classification.
   Unclassified data cannot be exposed. (You may classify it "public" — explicitly —
   but you cannot leave it blank and expose it.)

2. NO PII WITHOUT A GOVERNING POLICY
   A node classified PII/confidential MUST have a policy reference before any projection,
   AI pack, or export can include it. Sensitive + ungoverned = compile fails, every
   posture.

3. POLICY CANNOT FAIL OPEN
   If policy evaluation errors, is missing, or is undetermined for a node, the decision
   is DENY. Never allow-on-error. (Mirrors the false-negative discipline: an undetermined
   check fails closed, not open.)

4. SECURITY CHECKS CANNOT BE DISABLED, ONLY OVERRIDDEN-WITH-RECORD
   Built-in checks tagged `security` cannot be set to `off`. They can be explicitly
   overridden for a specific node only with a recorded, owned, expiring justification
   (an authored override node) — never silently switched off.

5. DENY-BY-DEFAULT IS THE FLOOR, NOT A SEED
   The seed RULES are wipeable; the deny-by-default POSTURE is not. With zero rules, a
   node is invisible, not public. Users open access explicitly; they cannot accidentally
   leave it open.

6. USER EXTENSIONS INHERIT THE FLOOR
   A user-defined aspect that holds data is subject to classification rules. A
   user-defined projection still routes through policy. A user-defined check runs in the
   sandbox and cannot grant access. Extensibility never escapes the floor — new kinds
   enter the system already governed.
```

### Why this keeps extensibility total *above* the floor

The floor constrains exactly six things, all of them about *not leaking data*. It does
not touch what metrics mean, who your roles are, what checks you write, what aspects you
invent, or how you present anything. A user can extend the system in every direction the
four axes allow — and every extension lands **already governed**, because the floor is
structural and new kinds inherit it. You get "define whatever you want" and "cannot open a
hole" at the same time, because the hole-opening moves are the only moves removed.

The distinction in one line: **posture is a dial on opinion; the security floor is a dial
that does not exist.** You can tell ClearMetric to stop nagging about style. You cannot tell
it to expose ungoverned PII, because that capability was never built.

---

## The five layers

```
SOURCES (any — quarantined behind adapters)
  warehouse INFORMATION_SCHEMA · dbt manifest · raw SQL ·
  query logs · authored intent (YAML)
                │
            ADAPTERS  (normalize any source → canonical graph fragments)
                │
1. COMPILER  (opinionated · strict · teaching · source-agnostic)
  ingest fragments → resolve LOGICAL identity (+ physical bindings) → build graph →
  derive (with state) → run CLEANER (built-in + user checks) → validate by rule-tier
                │
2. GRAPH  (truth — standardized · stores ownership refs · makes no authz decisions)
  typed nodes + typed edges + contracts ·
  logical canonical IDs + physical bindings · small core + appendable aspects ·
  provenance + derivation state on every fact
                │
3. POLICY  (intent — one engine, user-authored rules, three enforcement modes)
  (identity, node) → allow | deny | mask | filter
                │
4. PROJECTION  (lenses — computed per identity)
  view = contract/query over graph + persona lens, filtered by (3)
                │
            EMITTERS  (shape graph slice → any target format)
                │
5. TARGETS (any — quarantined behind emitters)
  docs (MD/HTML) · catalog · AI context pack · BI frontend contract ·
  lineage explorer · OpenLineage · compiled warehouse RLS / OPA bundle
```

One sentence: adapters normalize any source into one standardized graph; the source-agnostic
core resolves, derives, cleans, and governs that graph; projections filter it per identity;
and emitters shape the result into any target — the core in the middle knowing nothing about
either end.

---

## Layer 2 — The graph (where structure is rigid)

### Identity: logical node, physical bindings

"One canonical ID per real thing" is correct as a goal but becomes a philosophical
argument unless you separate the *logical* concept from its *physical* locations. The
stable node is **logical**. Where it lives is a list of **bindings**.

```yaml
id: column.fct_orders.net_revenue
kind: column
identity_scope: physical | logical | semantic | runtime
bindings:
  - warehouse: snowflake
    database: analytics_prod
    schema: marts
    table: fct_orders
    column: net_revenue
  - warehouse: snowflake
    database: analytics_dev
    schema: marts_dev
    table: fct_orders
    column: net_revenue
```

Policy, lineage, BI, and AI all attach to the stable **logical** node; the bindings tell
the system *where it physically lives* (dev vs prod, dbt-model vs warehouse-table). This
is what makes canonical identity implementable rather than aspirational, and it is the
single hardest technical piece — the identity resolver is built and stress-tested first,
before any module leans on it.

### Node base (stable, small)

```yaml
id: metric.finance.net_revenue        # canonical logical ID — required, unique
type: metric
name: Net Revenue
domain: finance
provenance: authored | derived        # which half produced this
lifecycle: draft | certified | deprecated
owner: team.finance_analytics         # a reference, NOT an authz decision
source_path: metrics/revenue.yml
```

The graph **stores ownership and identity references** (`owner: team.finance_analytics`)
but **makes no authorization decisions**. The graph may know who owns a thing; it never
decides who may *see* it. That decision belongs to policy (layer 3).

### Aspects (where content is flexible)

Independently attachable typed metadata; adding `usage` does not touch
`metric_definition`. Users may define new aspect types — the core only requires that an
aspect attach to a node by canonical ID. **Flexibility axis #1.**

```
metric_definition · lineage · usage · ai_behavior ·
quality · glossary · runtime_binding · <user-defined>
```

### Contracts (the primitive that makes the graph buildable)

A node *describes*. A contract makes it *bindable / executable*. This is the difference
between a metadata graph and infrastructure. A frontend, an AI agent, and a test bind to
the **contract**, never the raw node. **Contracts, not dashboards.**

```yaml
id: query.executive.revenue_by_month
type: query
inputs:
  parameters:
    start_date: date
    end_date: date
outputs:
  columns:
    month: date
    net_revenue: number
depends_on:
  - metric.finance.net_revenue
policy:
  required_projection: aggregate_only
runtime:
  compiled_sql: generated
```

### Derivation state (honesty — "with confidence, not magic")

Derived facts can be missing, partial, ambiguous, or wrong. sqlglot degrades on dynamic
SQL, macros, temp tables, UDFs, ambiguous aliases, cross-database refs. The graph records
the gap instead of pretending. Every derived fact carries:

```yaml
derivation:
  status: complete | partial | failed | skipped
  confidence: high | medium | low
  source: sqlglot | dbt_manifest | information_schema | query_logs
  errors: []
```

A known gap is safe; a silent wrong answer is fatal. The compiler requires authored
intent only where human judgment is needed; derived metadata is computed when possible,
stamped, and when derivation fails the gap is recorded — not hidden.

---

## The derived / authored line (lightweight governance)

| Derived (computed, stamped, never hand-typed) | Authored (intent — no artifact holds it) |
|---|---|
| column lineage + value-semantics (sqlglot) | metric meaning + canonical name |
| dependencies, data types, structure | ownership |
| grain (where inferable) | classification (PII / confidential) |
| usage / dead-asset detection | access policy |
| duplicate-formula detection (AST) | AI permissions |
| freshness | lifecycle decisions |
| impact analysis | glossary / synonyms |

---

## Layer: the Cleaner (built-in checks + user-defined checks — ONE mechanism)

The cleaner is **not a separate tool and not a new axis.** A check is just a function
that reads the graph and emits findings. Built-in checks and user checks run through the
**same check engine** in the compiler, declare the **same rule-tiers**, and read the
**same graph by canonical ID**. That is what keeps it one architecture: a duplicate check
and a user's custom test are the *same kind of thing* — `(graph) -> findings`.

### The check contract (what every check is)

```yaml
id: check.no_orphan_metrics
kind: check
scope: node | edge | graph          # what it traverses
selector: type == "metric"          # which nodes it applies to
tier: structural | error | warn | off
message: "metric {id} has no certified lineage to a source column"
fix_hint: "add depends_on or run `clearmetric lineage --repair {id}`"
provenance: builtin | user
```

A check returns findings:

```yaml
finding:
  check: check.no_orphan_metrics
  node: metric.sales.pipeline_value
  severity: error
  message: "..."
  fix_hint: "..."
```

### Built-in checks (ship by default)

```
identity:        no two logical nodes share a binding (structural)
                 every edge resolves to existing nodes (structural)
completeness:    every asset has an owner / classification / policy ref (error*)
non-duplication: duplicate-formula (warn by default — see below)
hygiene:         dead assets (unused / unviewed), orphan nodes,
                 undocumented columns, deprecated-metric still referenced
freshness:       stale derivations, low-confidence facts surfaced
```

`*` at strict posture; relaxes by posture (see Opinionation).

### User-defined checks (the extensibility you asked for)

Users write their own checks the same way built-ins are written — as a check node with a
selector and a tier, or as code against the graph query API. They run in the same pass,
emit the same findings, honor the same posture. Examples a team might add:

```yaml
- id: check.revenue_metrics_must_have_currency
  selector: type == "metric" and domain == "finance"
  tier: error
  message: "finance metric {id} missing currency aspect"

- id: check.no_pii_in_ai_context
  selector: aspect.ai_behavior.allowed == true
  tier: error
  message: "{id} exposed to AI but has PII classification"
```

Because user checks are nodes on the graph, they are themselves versioned, owned,
testable, and visible — not hidden scripts. **Flexibility axis #4: users define their own
cleaning, duplication, and test logic without leaving the architecture.**

**User checks inherit the security floor (they cannot open a hole):** a user check runs in
a read-only sandbox over the graph. It can *report* findings at any tier, but it cannot
grant access, mutate the graph, or set a `security`-tagged built-in check to `off` (floor
item 4). Users can make the cleaner *stricter* freely; they can only make it *less strict*
on non-security checks, and only with a recorded override. So the worst an inexperienced
user can do with a custom check is add noise — never remove a guardrail.

### Why the cleaner is not a fourth pipeline

It reads the graph (layer 2) and reports through the rule-tier system (the compiler's
existing enforcement). It writes nothing new and decides no access. So it is the compiler
*using* the graph, not a parallel system. One architecture holds.

---

## Non-duplication: warn by default, fail-closed only on real collision

Two formulas can be byte-identical and *mean* different things (domain, grain, currency,
audience, timing, inclusion rules). Hard-failing on AST match alone makes experienced
teams fight the tool. So duplicate detection is **warn by default**, and only escalates
to fail-closed when the project opts in OR when collision is unambiguous:

```
strict fail-closed ONLY when:
  same expression  AND  same grain  AND  same filters
  AND (same domain OR overlapping certified lifecycle)
otherwise: warn with a choice.
```

The teaching finding:

```
Possible duplicate detected:
  metric.sales.revenue and metric.finance.net_revenue — 94% formula similarity.
Choose:
  1. reuse existing metric
  2. create alias
  3. mark intentionally separate (with reason)
```

"No duplicates" stays a guarantee of *identity* (structural, impossible to violate) and a
*guided* property of *meaning* (warn, user decides) — never a blunt fail on content.

---

## Layer 3 — Policy (flexible content, one engine, honest enforcement)

You ship the **vocabulary** (policy kinds) and the **evaluation engine** plus safe seed
defaults. The org authors the **rules** and can wipe every seed; the engine still works.
You never ship verdicts. Policy-as-data:
`decision = f(identity_attributes, node_attributes, org_rules)`.

Wiping the seeds removes the example **rules**, not the **floor**: with zero rules a node
is invisible (deny-by-default, security-floor item 5), never public. And policy
evaluation that errors or is undetermined returns DENY (floor item 3), never allow. Users
open access explicitly; they cannot wipe their way into an open default.

Policy kinds: `RBAC` · `RLS` · `masking` · `AI-permission` · `export`. Fixed kinds,
user-authored rules. **Flexibility axis #2 — users define every permission.**

### Three enforcement modes (never claim universal enforcement)

```
Native enforcement   compiled INTO the target — Snowflake / BigQuery row policies,
                     Postgres RLS, OPA bundle. Actually enforced at the data.
Runtime enforcement  the ClearMetric query / projection / AI-context API.
                     Enforced IF the consumer uses your runtime.
Advisory             docs, catalog annotations, generated-but-unapplied configs.
                     NOT enforced.
```

Honest claim: **all ClearMetric-native authorization is layer 3; external authorization is
enforced only when compiled into the target or routed through the runtime.** A consumer
that exports the graph into Power BI or a custom frontend bypasses runtime policy unless
policy was compiled native into the warehouse. Stated plainly — in a governance tool,
claiming enforcement you do not have is a liability.

---

## Layer 4 — Projection (personas are filters, not forks)

A view = a contract/query over the graph + a persona lens, evaluated through the policy
filter for the asking identity. Same graph, different lens.

- "Full catalog" = projection selecting all kinds, admin identity.
- "Full lineage" = projection traversing lineage edges, no depth limit.
- "Custom AI context" = scope (projection) + permission (policy) + shape (serialization),
  BYO-model, BYO-key. The product produces the pack; the user's AI consumes it.

A per-role BI view and a per-role AI context are the *same operation*. Users define their
own personas, views, serializations. **Flexibility axis #3.**

---

## The extension axes (this is what "everything extensible" means)

Four orthogonal axes, none touching the others, because identity-filtering lives in one
place (layer 3) and truth in one place (layer 2):

1. **Graph kind** — new node / edge / aspect → layer 2 grows; others just see new nodes.
2. **Policy kind / rule** — new role / masking rule → layer 3 grows; filter resolves
   differently.
3. **Projection kind** — new persona / view / AI pack → layer 4 grows; a new lens.
4. **Check** — new built-in-style or user check → runs in the cleaner pass; reads the
   graph, reports through the tier system. No new pipeline.

Any "what if they want X" resolves to one of these. If it cannot, that is the only signal
the core is genuinely missing something.

---

## Opinionation — a rule-tier system, with a hard floor

Every rule AND every check declares a tier. Tiers are the mechanism; posture presets are
the user-facing setting.

```
structural   the bad state is IMPOSSIBLE to represent.
             (one logical node per real thing; edges must resolve)
             requires zero discipline; NEVER part of any setting. The floor.
error        expressible but the compiler FAILS CLOSED, no outputs until fixed.
             (completeness; unambiguous duplicate; user error-tier checks)
warn         usually wrong, sometimes right → warn, proceed.
             (formula similarity by default; hygiene findings)
off          disabled.
```

Map onto the derived/authored line: hard-enforce **structure** (always) and
**completeness** (error at strict); **guide** on **content judgment** (warn). Force the
floor, guide the ceiling.

### Teaching findings

Every error/warn finding states *what is wrong, why it matters, and the one fix*. The
defaults and scaffolding embody best practice (`init` produces the right layout; node
templates pre-stamp required fields; policy is deny-by-default), so doing nothing yields
correct structure and the findings teach the rest. The inexperienced user falls into the
pit of success.

### Posture is a setting — structural floor is not

```
strict      (default for init) completeness at error · full teaching cleaner
standard    structural + security floor (always) · completeness as warnings
permissive  structural + security floor ONLY · all style/completeness off ·
            get out of the way — but the security floor still holds
```

Project-level posture with explicit, **recorded** per-rule and per-check overrides.
**Structural integrity AND the security floor are never part of the setting** — both are
impossible to violate in every posture, including `permissive`. The dial governs *opinion
and hygiene*, never *integrity or security*. Even `permissive` cannot corrupt the core or
open a data hole; it only silences style and completeness nagging.

---

## Why opinionated and flexible coexist

| Concern | Rigid (opinionated) | Flexible (user-defined) |
|---|---|---|
| Identity | one logical node per thing — structural, always | IDs / domains / names / bindings are yours |
| Provenance | every fact stamped derived/authored — structural | what you author is yours |
| Completeness | owner/classification/policy required at strict — error | the values are yours |
| Non-duplication | identity dup impossible (structural); formula dup warns | reuse/alias/separate is your call |
| Checks | run through one engine + tier system | write any check you want (axis #4) |
| Node/aspect kinds | must use node base + attach by ID — structural | invent any aspect/kind (axis #1) |
| Policy | one engine, three honest modes — mechanism | every rule and role is yours (axis #2) |
| Projection | must route through policy — structural | every persona/view is yours (axis #3) |
| Posture | structural + security floor non-negotiable | error/warn/off is your setting |
| Security | floor is structural — cannot expose ungoverned data, ever | every role, rule, and classification value is yours |

Rigidity is always about *shape, identity, integrity, and not leaking data*. Flexibility
is always about *content, meaning, access rules, and the checks you run*. They never touch
the same layer.

---

## The MVP — core plus two modules, built interleaved

A core validated against zero modules is a hypothesis. Build the two modules *with* the
core so each hardens it; fix the core whenever a module makes it awkward, while cheap.

### Core (open source, Apache 2.0, local — engine and formats both open)
```
project schema · logical IDs + bindings · compiler · graph store ·
node/edge/aspect model · contract primitive · provenance + derivation-state ·
cleaner (check engine: built-in + user checks) · rule-tier validator · graph query API
```

### Module A — Live query primitive (proves the graph is buildable)
```
metric definition · query definition · contract ·
compiled SQL · query endpoint · result schema · frontend binding contract
```
Proves the graph can compile/execute live queries a frontend binds to. Not a renderer.

### Module B — Derived lineage / catalog (proves the graph holds derived truth)
```
dbt manifest ingestion · raw SQL ingestion · information_schema ingestion ·
model/column lineage (with derivation state) · metric dependency graph ·
impact analysis · minimal catalog projection
```

Both read the same nodes by the same canonical IDs. One executes, one traverses, on one
graph — turning "extensible" from promise into demonstrated fact.

### Brutally simple I/O
```
Input:   dbt manifest · SQL folder · information_schema · YAML metrics & queries
Output:  compiled graph JSON · impact CLI · query endpoint ·
         minimal catalog JSON · frontend contract JSON · cleaner report
```

### First demo
```bash
clearmetric scan
clearmetric compile
clearmetric clean                       # built-in + any user checks
clearmetric impact column.fct_orders.net_revenue
clearmetric serve
clearmetric query query.executive.revenue_by_month
```
Then show the same metric powering: live query result · lineage traversal · catalog
projection. That proves the thesis.

### Explicitly NOT in the MVP (keep attachment placeholders)
```
full RLS · full RBAC · full AI agent · full dashboard builder ·
full policy compiler · full usage analytics · full catalog UI · approvals
```
```yaml
security: { classification: internal, policy_refs: [] }
ai:       { allowed: true, notes: [] }
usage:    { tracking_enabled: false }
```

---

## The four invariants (non-negotiable, every posture)

1. **One logical node per real thing**, with physical bindings — the same node to
   lineage, policy, usage, and BI.
2. **The graph never forks per persona** — personas are projections, never copies. The
   graph stores ownership refs but makes no authorization decisions.
3. **All ClearMetric-native authorization is layer 3**; external is enforced only when
   compiled to target or routed through runtime — stated honestly.
4. **Provenance + derivation state stamped on every fact** — derived vs authored explicit;
   derived facts carry status/confidence; the compiler requires only the authored half.
5. **The security floor is structural** — no unclassified exposure, no ungoverned PII,
   policy fails closed, security checks cannot be disabled, deny-by-default, and user
   extensions inherit all of it. No posture and no extension can lower it.

---

## The source-agnostic core — adapters in, emitters out, core knows neither

**The core does not care where inputs come from or where outputs go.** It operates *only*
on the standardized graph. Source-specific concerns are absorbed by **adapters** on the way
in; target-specific concerns are absorbed by **emitters** on the way out. Between them sits
a core that knows nothing about Snowflake, dbt, Power BI, Markdown, or any LLM — it knows
only nodes, edges, contracts, aspects, and provenance. This is the single most important
boundary in the system: it is what makes the primitives genuinely reusable instead of
secretly coupled to one stack.

```
  SOURCES (any)              ADAPTERS            STANDARDIZED CORE           EMITTERS           TARGETS (any)
  ───────────               (normalize in)       ─────────────────         (shape out)         ───────────
  warehouse schema    ─┐                         ┌───────────────┐                        ┌─→  docs (MD/HTML)
  dbt manifest         ├─→  ingestion adapter ─→ │  ONE GRAPH     │ ─→ projection/emit ─→  ├─→  catalog
  raw SQL              │    (→ graph fragments   │  nodes·edges·  │    (graph slice →      ├─→  AI context pack
  query logs           │     in canonical form)  │  contracts·    │     target format)     ├─→  frontend contract
  authored YAML        │                         │  aspects·      │                        ├─→  OpenLineage
  <any future source> ─┘                         │  provenance    │                        ├─→  warehouse RLS / OPA
                                                 └───────────────┘                        └─→  <any future target>
```

### The contract that makes this hold

- **Adapters normalize IN.** An adapter's only job is to read a source and emit graph
  fragments in the canonical format, stamped with derivation state
  (status/confidence/source). The core receives only canonical fragments. It never parses
  Snowflake DDL or a dbt manifest itself — the adapter did that and handed over standard
  nodes/edges.
- **The core touches only the standard graph.** Identity resolution, lineage, the cleaner,
  policy, contracts — all operate on canonical nodes by canonical ID. None of them contains
  a branch on "if source == snowflake." Remove every adapter and the core still compiles,
  validates, and queries a graph; it simply has nothing in it.
- **Emitters shape OUT.** An emitter takes a graph slice (already filtered by policy via a
  projection) and serializes it to a target format — Markdown docs, an HTML catalog, an AI
  context pack, a frontend contract, OpenLineage events, a warehouse RLS policy. The core
  never knows the target exists. A docs page and an AI context pack are the *same* core
  operation (projection) with *different emitters*.
- **One standardized format is the spine.** Because everything in is normalized to the
  canonical graph and everything out is emitted from it, the graph format is the universal
  interchange. Adapters and emitters are the only source/target-aware code; they are
  pluggable leaves, never core.

### Why this is the whole point

This is what "infrastructure primitives" actually means: the primitives operate on a
standard, and the messy reality of specific warehouses and specific output targets is
quarantined at the edges. Consequences:

- **Input-agnostic by construction.** A dbt shop, a raw-SQL shop, and a warehouse-only shop
  all produce the same graph — different adapters, identical core, identical downstream.
  Supporting a new warehouse or source is a new adapter, never a core change.
- **Output-agnostic by construction.** Automated documentation, a catalog, AI context, a BI
  frontend contract, and compiled policy are all emitters over the same graph — different
  emitters, identical core. Supporting a new output is a new emitter, never a core change.
- **Adapters and emitters are the same kind of extension as everything else.** They join the
  extension axes: a source is "input you normalize," a target is "output you emit," and both
  are pluggable leaves governed by the same standard. The core's surface stays small and
  stable precisely because source and target variability live at the edges, not the center.

### Warehouse: a first-class adapter, not a core dependency

The warehouse connection is important to the *product* but it enters the core as an
**adapter** like any other — it does not privilege the core with warehouse-specific logic.
A warehouse adapter does three jobs, all of which produce or validate standard graph facts:

1. **Metadata source** — reads INFORMATION_SCHEMA (databases, schemas, tables, columns,
   types, views, comments, freshness where available) and emits **physical bindings** onto
   logical nodes. This is what makes identity concrete.
2. **Validation source** — confirms against live reality: does this table/column still
   exist, did the schema drift, does compiled SQL run, does a contract's output match. This
   is strictly stronger than static parsing and is the warehouse's sharpest contribution.
3. **Runtime target** — executes a compiled query contract against the warehouse and returns
   results. (This is an *emitter/runtime* concern — the live-query path — and is the widest,
   most expensive part; see staging below.)

Crucially, jobs 1 and 2 are **read-only metadata/validation** and cheap; job 3 (live
execution) is the expensive runtime. The core treats all three as adapter/emitter
capabilities, not core logic — so "warehouse-connected" is an identity of the *product*
delivered through the *edges*, while the core stays source-agnostic.

### Deployment is the same principle, applied to runtime

The graph artifact is also the contract between *where it compiles* and *where it runs*. One
open compiler produces one `graph.json`; the runtime around it varies (local / self-hosted /
managed) without the graph changing. One compiler, never two — the hosted product persists
and serves the same artifact, never reinterprets it. Portable in and out is the anti-lock-in
guarantee: the artifact you deploy is the artifact you can leave with.

---





## Distribution — open-source core, paid managed layer (the dbt pattern)

The decision: **open-source the entire local core, engine included, under Apache 2.0.
Charge for the operational system-of-record around the graph over time.** This follows the
path dbt proved — dbt Core was Apache 2.0 from day one, which is *why* it became the
standard (free, inspectable, no procurement, bottom-up adoption), and the revenue came
from the operational layer (dbt Cloud), never from the engine.

For a foundation play, an open engine is not the moat given away — it is the only path
*to* the moat. A closed engine would have prevented dbt from ever becoming dbt. The moat
is being the adopted default *and* owning the system-of-record where history accumulates —
neither of which a closed engine helps with, and both of which open adoption accelerates.
This also resolves the false tension between "don't let a better-distributed player take
it" and "make it free": open the engine (adoption), keep the operational layer paid
(revenue + the real, fork-proof moat).

### Open — Apache 2.0, `clearmetric-core` (and the spec)

The full local core. "Core" is accurate here because the core engine genuinely is open
(the dbt Core / Cube Core / Metabase convention):

```
local compiler · identity resolver · graph builder ·
SQL/dbt lineage derivation · metric/query compiler · contract engine ·
cleaner / check engine · policy evaluator · projection engine ·
graph + node/edge/aspect schema · contract & derivation-state formats ·
policy-as-data format · CLI · graph query API · ingestion adapters · examples
```

Everything a developer needs to adopt ClearMetric as a standard is free and inspectable.
Artifacts emit in open formats — **OpenLineage, JSON Schema, OpenAPI, OPA bundles** (OSI is
a *semantic spec*, not an artifact format) — so the org owns its graph and can always
leave. Because the engine is open, the quality bar is the pitch: the code being public
means the resolver simply has to work on real, messy SQL — there is no hidden secret sauce
to hide behind.

### Paid — managed system-of-record (`clearmetric-cloud`)

The value here is *accumulated*, so it is paid and fork-proof: hosted graph **history**
(the persisted, time-versioned graph — what a metric meant last quarter, who changed what,
the decision trail), collaboration, SSO/RBAC at team scale, audit, managed runtime, policy
*deployment* to warehouses, enterprise connectors, support.

Why this is the durable moat even with the engine fully open: a better-distributed player
can fork the open engine, but cannot fork the history living in *customers'* instances —
and that history is exactly the part frontier models cannot reconstruct, so it does not
compress as models improve. The commercial opportunity is never the first `compile`
command; it is the system of record around the graph over time. **Moat, paid tier, and
model-resistance are the same thing.**

### Repo structure

```
clearmetric-core     Apache 2.0 · public · the full local engine + CLI + formats
clearmetric-spec     Apache 2.0 · public · schemas · OpenAPI · examples · fixtures
                     (may live inside core initially; split out if it earns its own life)
clearmetric-cloud    paid · hosted history · collaboration · audit · policy deployment
```

### What the docs/site should say

ClearMetric Core is open source (Apache 2.0): the local compiler, graph, lineage, cleaner,
and CLI are free, inspectable, and yours. The managed platform — hosted history,
collaboration, governance at team scale — is optional and paid. (This claim is now true:
the engine is OSS, so "open source" is accurate, not overclaimed.)

---

## Positioning

**For buyers:**
> A compiled analytics graph that turns code-defined metrics, queries, lineage, metadata,
> and policy intent into reusable infrastructure for BI, AI, and governance.

**For the first developers (the wedge):**
> Live analytics primitives and derived lineage from the same codebase.

Central concrete phrase:
> ClearMetric turns analytics definitions into queryable graph nodes and bindable
> contracts.

---

## The five hard constraints (obey or it fails)

1. Do not build the giant platform first — core plus two modules, interleaved.
2. Do not claim perfect derivation — track coverage, confidence, gaps.
3. Do not claim universal policy enforcement — native / runtime / advisory, stated.
4. Do not tell buyers to build their own BI tool — they get frontend control over
   governed analytics primitives.
5. Do not separate core from modules — live query and lineage from day one.