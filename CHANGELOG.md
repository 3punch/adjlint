# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and the
project's interpretation of semantic versioning: breaking changes to rule codes,
finding shapes, or the factor formula bump the major version, because a rule that
starts or stops firing silently is a breaking change for a CI gate.

## 0.1.0

First release: the rule engine, the CLI, and ten rules.

### Added

- `adjlint audit` -- lint one or many instruments from CSV, with `text`, `md` and
  `json` reports and CI-friendly exit codes (`0` clean, `1` findings, `2` usage).
- Corporate-action reference-price reconstruction for splits, bonus issues
  (capital increases), cash dividends, rights issues and mixed events, expressed
  as one formula over per-share terms.
- Backward adjustment-factor chains at fixed 10-decimal precision, so runs are
  byte-reproducible.
- Rules `AL001`-`AL010`: band-limit breach, action-not-applied, unclassified gap,
  duplicate event, robust outlier return, calendar misalignment, adjustment
  mismatch, OHLC inconsistency, frozen-price streak, base-price disagreement.
- `MarketProfile` price-band ladders with tick size and weekday calendar; bundled
  `generic` and `tse` profiles.
- `TradingCalendar` from an explicit session list or a profile weekday rule; the
  "session with no bar" check only runs against an explicit calendar.
- CSV readers that tolerate UTF-8 BOM, thousands separators, Persian and
  Arabic-Indic digits, `YYYY/MM/DD`, and UTC timestamps floored in the market
  timezone.
- Library API: `audit`, `audit_many`, `SymbolSeries`, `AuditConfig`,
  `resolve_actions`, `adjusted_series`, `adjustment_factor`, `render`.
- `adjlint rules` and `adjlint profiles` introspection commands.
- 65-test suite; CI on Python 3.10-3.13 with a job that asserts the README's
  worked example and the zero-dependency promise.

### Decisions

- No `--fix`, and no imputation. Missing reference prices are reported as
  `AL003` rather than guessed.
- No downloader, storage layer or scheduler. `adjlint` judges data it is given.
- Band-limit presets are configuration starting points; exchange policy numbers
  must be verified against the current directive.
