# adjlint

**A linter for OHLCV price data.** It reconstructs what each corporate action
*should* have done to the price, checks whether your feed actually did it, and
flags every move that no booked event explains.

```
adjlint 0.1.0 | profile=tse | calendar=tse | symbols=1 | bars=12 | events=3

  warning WEBM     2026-06-01 AL003 unclassified-gap
          no reference price: the series has no close before the effective date and the
          action declares no previous_close -- the event is labelled rather than guessed
  error   WEBM     2026-06-16 AL001 band-limit-breach
          daily move 14.70% is outside the 5.00% band with no corporate event booked
            prev_close=830  expected_base=830  band=5.00%  observed=14.70%
  error   WEBM     2026-06-18 AL002 action-not-applied
          1 booked event(s) imply a -2.12% reset but the session opened -0.11% off the
          previous close
            expected=-2.12%  observed=-0.11%  price_field=open

2 errors, 1 warning
```

A bad adjusted-price series does not fail loudly. It quietly reruns your
backtest into a different strategy, and you find out months later when the
live numbers refuse to match. `adjlint` puts the check where it belongs: in
ingest, in CI, before the data reaches a model.

## Why this exists

- **Split and dividend bugs are the most expensive silent bug in market data.**
  A missed 50% capital increase looks exactly like a stock that doubled.
- **Vendor `adj_close` columns are not audited anywhere.** They are copied,
  trusted, and compounding errors into every return you ever compute.
- **Existing libraries fetch data. None of them check it.** `adjlint` is
  deliberately not a downloader -- point it at whatever you already have.

## Highlights

| | |
| --- | --- |
| **Zero runtime dependencies** | pure standard library. A quality gate that drags in a dependency tree is a gate nobody keeps in CI |
| **`Decimal` end to end** | adjustment factors are compounded across years of history; float drift has no business being in that product |
| **Never imputes** | an event with no derivable reference price is reported as `AL003 unclassified-gap` for a human, not silently filled |
| **CI-native** | `--format json`, exit code `1` on findings, `--fail-on` to choose what blocks a merge |
| **Regional-export tolerant** | UTF-8 BOMs, thousands separators, Persian/Arabic-Indic digits, `YYYY/MM/DD`, and UTC timestamps floored in the *market's* timezone |

## Install

`adjlint` is not on PyPI yet. From a checkout:

```bash
pip install .
```

Or run it straight from the source tree without installing:

```bash
PYTHONPATH=src python -m adjlint --help
```

Python 3.10+.

## 30-second quickstart

Two CSVs in, one report out:

```bash
adjlint audit --prices examples/prices.csv --actions examples/actions.csv --profile tse
```

`prices.csv` needs `date` and `close` (plus optional `symbol,open,high,low,volume`);
`actions.csv` needs `symbol`, `kind`, `effective_date` and the economic terms of
each event:

```csv
symbol,kind,effective_date,cash_per_share,bonus_ratio,subscription_price,subscription_ratio
WEBM,dividend,2026-06-01,30,,,,
WEBM,bonus,2026-06-08,,0.5,,,
WEBM,dividend,2026-06-18,20,,,,
```

Gate an ingest job on it:

```bash
adjlint audit -p prices.csv -a actions.csv --format json --fail-on error || exit 1
```

## The rule catalog

`adjlint rules` prints this table.

| Code | Default | Name | Catches |
| --- | --- | --- | --- |
| `AL001` | error | `band-limit-breach` | Daily move outside the exchange price band with no booked event |
| `AL002` | error | `action-not-applied` | A booked split/bonus/dividend/rights the price series ignored |
| `AL003` | warning | `unclassified-gap` | Event on record with no derivable reference price |
| `AL004` | error | `duplicate-event` | The same event booked twice on one effective date |
| `AL005` | warning | `outlier-return` | Robust (median/MAD) outlier among returns on quiet days |
| `AL006` | error | `calendar-misalignment` | Duplicate, unsorted, or off-calendar bars; missing sessions |
| `AL007` | error | `adjustment-mismatch` | Vendor `adj_close` disagreeing with a recomputed factor chain |
| `AL008` | error | `ohlc-inconsistency` | Non-positive prices, inverted ranges, negative volume |
| `AL009` | warning | `frozen-price-streak` | Identical closes across N sessions -- a feed that stopped updating |
| `AL010` | warning | `base-price-disagreement` | A declared base price that contradicts the series close |

## How the maths works

Every action type collapses into one formula, because each action is expressed
per share held *before* the event:

```
reference = (previous_close - cash + subscription_ratio * subscription_price)
            / (1 + bonus_ratio + (split_ratio - 1) + subscription_ratio)

factor    = reference / previous_close
```

`reference` is the theoretical opening level on the ex-date -- what the exchange
calls the base price. Prices older than the event are multiplied by the
cumulative product of the factors that follow them, which keeps returns
continuous in current share terms.

Worked example, a 50% capital increase from retained earnings on a close of 1000:

```python
>>> from decimal import Decimal
>>> from adjlint import ActionKind, CorporateAction, adjustment_factor
>>> from datetime import date
>>> bonus = CorporateAction("WEBM", ActionKind.BONUS, date(2026, 6, 8), bonus_ratio=Decimal("0.5"))
>>> adjustment_factor(Decimal("1000"), bonus)
Decimal('0.6666666667')
```

Factors are quantised to 10 decimal places so a run is byte-reproducible on
every machine.

## As a library

```python
from decimal import Decimal

from adjlint import AuditConfig, SymbolSeries, audit, get_profile, load_actions, load_prices

bars_by_symbol = load_prices("prices.csv", timezone=get_profile("tse").timezone)
actions = load_actions("actions.csv")

findings = audit(
    SymbolSeries.of("WEBM", bars_by_symbol["WEBM"], actions),
    profile=get_profile("tse"),
    config=AuditConfig(tolerance=Decimal("0.002")),
)

for finding in findings:
    print(finding.severity.value, finding.on, finding.code, finding.message, finding.details)
```

`audit_many()` takes several instruments and merges the findings in
`(symbol, date, code)` order.

## Market profiles

A profile is the microstructure needed to judge a move: the price-band ladder,
the tick size, the weekday calendar, and the timezone used to floor timestamps.

```bash
adjlint profiles          # dump the bundled profiles as JSON
adjlint audit -p prices.csv --profile tse
```

Two profiles ship with the tool:

- **`generic`** -- flat +/-20% band, Mon-Fri. Right default for anything you
  know nothing about, including crypto and US equities on a daily frame.
- **`tse`** -- Tehran Stock Exchange shape: Friday weekend, tick size, and an
  illustrative price-band ladder.

> [!WARNING]
> Band limits are **market policy**, and policy changes. The bundled `tse`
> ladder is a starting point for configuration, not an authoritative quotation.
> Verify the current directive, then override the profile in your own config --
> `validate()` will reject a malformed ladder so a typo fails at startup instead
> of producing false findings.

For any other market, describe it in a JSON file and pass `--profile-file`
(it takes precedence over `--profile`). The file holds **one** profile object —
one entry of what `adjlint profiles` prints, not the whole catalog; saving the
command output directly is the most common mistake and is rejected by name.
Only `name` is required, and omitted keys keep their defaults:

```json
{"name": "bse", "timezone": "Asia/Kolkata", "weekend_days": [6],
 "band_ladder": [["250", "0.05"], [null, "0.02"]], "price_tick": "0.05"}
```

Write decimals as strings (`"0.05"`, not `0.05`): a JSON number is a float, and a
float is rejected rather than rounded into a band limit. `null` means "absent"
for `price_tick` and for the last rung's bound. A bad field or rung exits with
code `2` and names it.

Supply the exact session calendar when you have one; `AL006` only reports
sessions with no bar when the calendar is explicit, because a weekday rule can
never know about ad-hoc market closures.

```bash
adjlint audit -p prices.csv --calendar sessions-2026.csv
```

## Configuration

| Flag | Default | Meaning |
| --- | --- | --- |
| `--tolerance` | `0.005` | Relative slack on "the expected move happened" |
| `--outlier-z` | `8` | Robust sigma beyond which a quiet daily return is an outlier |
| `--outlier-min` | `20` | Minimum observations before `AL005` is allowed to speak |
| `--stale-streak` | `5` | Identical closes that count as a frozen feed |
| `--enable` / `--disable` | all | Select rules by code |
| `--fail-on` | `error` | `error`, `warning`, `info` or `never` |
| `--delimiter` | sniffed | Field separator for **every** input at once (`,`, `;` or tab); force one for exports detection cannot settle |

Exchange and broker exports often arrive semicolon- or tab-separated. The
delimiter is sniffed from the first lines of each file, then checked against the
columns that reader needs: a separator that leaves the date or close column
missing loses to one that does not. Detection falls back to `,`.

`--delimiter ';'` skips detection entirely. It applies to every input in the run,
so it only helps when all of them share that separator -- convert a mixed set to
one delimiter first. When detection cannot settle on any candidate, the
`ambiguous delimiter` error prints the header it parsed; other failures report
the missing column instead, because a missing column is not a delimiter problem.

## CI

```yaml
- name: Lint market data
  run: |
    pip install adjlint
    adjlint audit -p prices.csv -a actions.csv --format md --output adjlint-report.md
- name: Keep the report
  uses: actions/upload-artifact@v4
  with:
    name: adjlint-report
    path: adjlint-report.md
```

Exit codes: `0` clean, `1` findings at or above `--fail-on`, `2` usage or
unreadable input.

## Design decisions

- **Report, never repair.** `adjlint` has no `--fix`. A price series that needs
  fixing needs a decision from whoever owns the data, and a linter that quietly
  rewrites history is how you end up unable to reproduce last quarter's PnL.
- **Gaps are named, not filled.** `AL003` exists so that a missing reference
  price survives to the report instead of becoming an invented 0.999 factor.
- **No downloader, no storage, no scheduler.** Point it at a CSV, a DataFrame
  you already exported, or your ingest hook.
- **Determinism is a feature.** Fixed factor precision and stable sorting mean
  two runs on the same bytes print the same diff.

## Roadmap

- [ ] `--dates=jalaali` for Jalali-labelled exports (opt-in, so a wrong calendar
      is never a silent default)
- [ ] A `pandas`-free DataFrame adapter plus a `polars` bridge
- [ ] EOD/minute bars and a session-partition rule (`AL011`)
- [ ] `adjlint init` writing a per-exchange profile to `adjlint.toml`
- [ ] A GitHub Action published to the Marketplace
- [ ] More bundled profiles: US equities, crypto, and other regional exchanges

Want one of these? Open an issue -- shape beats polish here.

## Contributing

`git clone`, `pip install -e ".[dev]"`, `pytest`. Every rule ships with a
positive and a negative test; a rule without both is treated as unfinished.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT -- see [LICENSE](LICENSE).
