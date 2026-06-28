# Adoption gate

Backbone Phases 1–6 require **external adoption evidence** before execution. This gate is intentionally hard.

## Status

**Gate: NOT PASSED** (as of Backbone v2 implementation)

## Requirements

Pass **all** of:

- [ ] Wedge v1 checklist green in CI
- [ ] Wedge used by at least one **real user who is not the implementer**
- [ ] **External pull on record** below with named asker, verbatim quote, and link

## External pull record

| Field | Value |
|-------|-------|
| **Asker** | _TBD — person, team, or paying org_ |
| **Verbatim quote** | _TBD — what they asked for (metrics? gated export? runtime?)_ |
| **Link** | _TBD — GitHub issue, email thread, or customer ticket_ |
| **Date recorded** | _TBD_ |

## What fails the gate

- "The plan looks good"
- "Internal decision"
- "We'll need this eventually"
- Momentum without a named external asker

If the gate fails: **stop at Phase 0** for product scope expansion. Phase 0 (GraphView) improves the wedge without expanding the product promise.

## Note on Backbone v2 code

Implementation of Phases 1–6 may exist in the repository for review and testing, but **shipping** those features to users should wait until this gate passes with real evidence filled in above.
