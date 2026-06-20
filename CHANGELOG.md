# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0 - Unreleased

Initial public OSS release candidate.

### Changed

- migrated the repo to the `CatalogKit` namespace-package monorepo layout
- renamed the shared distribution to `catalogkit-core`
- renamed the query tool distribution to `catalogkit-query`
- added the thin `catalogkit` meta-package for convenience installs

### Added

- deterministic relation extraction for supported single-statement SQL inputs
- canonical `QueryMap` artifact with stable top-level shape
- CLI text and JSON output modes
- contract docs, governance files, and release validation workflow

### Supported

- `SELECT ...`
- `INSERT ... SELECT ...`
- `CREATE ... AS SELECT ...`

### Deferred

- output column lineage
- join semantics beyond dependency mapping
- wrapper target outputs
- warehouse-aware `SELECT *` expansion
