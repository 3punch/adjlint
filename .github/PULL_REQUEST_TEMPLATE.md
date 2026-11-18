<!--
Keep it short. A rule change that arrives with a real failure case and a test is
far more useful than a long description.
-->

## What changes, in one sentence

## Which rule, and why it earns its place

Rules that fire in someone's CI are a cost. If this adds or re-levels a rule, say
what it catches that nothing else does, and what it will now report that it did
not before.

## Failure case

Real (anonymised) bars and actions that behave differently before and after.
Paste in CSV shape — do not attach a dataset.

- [ ] I have run `pytest` and `ruff check src tests tools`
- [ ] A new rule has one positive and one negative test, and a `RULES` entry so it
      appears in `adjlint rules`
- [ ] If the change makes a report louder, I updated the README worked example and
      `tools/check_readme_contract.py`
- [ ] If anything user-visible changed, `CHANGELOG.md` has an Unreleased line

## Notes for review

Reproducibility is a property of this project, not a nice-to-have: factors are
compounded across years of history at a fixed working precision. If you touched
`adjust.py`, generate the JSON report twice and diff it —

```bash
adjlint audit -p examples/prices.csv -a examples/actions.csv --profile tse \
  --format json --fail-on never -o a.json
adjlint audit -p examples/prices.csv -a examples/actions.csv --profile tse \
  --format json --fail-on never -o b.json
diff a.json b.json
```
