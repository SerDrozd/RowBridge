# Changelog

## 0.7.0 - 2026-10-04

### Added

- saved run history with newest-first pagination;
- reopening previous reconciliation results from the local history page;
- current auto, human, review, and unmatched counts for saved runs;
- explicit run deletion confirmation with cascading cleanup of related local data.

## 0.6.1 - 2026-10-04

### Fixed

- runtime version reporting now follows the package version;
- Unicode letters are preserved during text normalization;
- Excel datetime values participate in date matching;
- common US and European amount formats, including accounting-style negatives, are parsed consistently;
- optional matching rules keep their documented base weights before active weights are normalized.

### Changed

- matching documentation now describes the score-ordered one-to-one selection behavior precisely;
- public documentation and UI copy were simplified and made consistent.

## 0.6.0

Initial public release.

### Added

- CSV and XLSX ingestion with preview and validation;
- candidate generation and typed matching rules;
- one-to-one reconciliation with ambiguity review;
- human accept, reject, and manual-link actions with audit history;
- SQLite persistence, pagination, and large-run safeguards;
- CSV and XLSX exports with formula-injection mitigation;
- local web protections and staged-file cleanup;
- architecture, security, and contribution documentation;
- CI package build verification and dependency update configuration.
