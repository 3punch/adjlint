# Contributing

Small, reviewable pull requests are preferred. If you want to add a rule, open an
issue first with a real (anonymised) failure case: the number of rules is the
product's cost, not its feature count, and every rule that fires in someone's CI
has to earn its place.

## Setup

```bash
git clone https://github.com/3punch/adjlint.git
cd adjlint
python -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest
```

No build step, no services, no network. The suite runs in well under a second.

## Adding a rule

1. Register it in `src/adjlint/rules.py` with the `@rule` decorator: a stable
   `ALnnn` code, a kebab-case name, a one-line summary, and the default severity.
   Codes are append-only -- never reuse or renumber one.
2. Yield `Finding` objects through `ctx.finding(...)`, and put anything a user
   would act on into `details`. A finding that cannot be acted on from the report
   alone is not finished.
3. Add **both** a positive and a negative test to `tests/test_rules.py`: the case
   that must fire, and the near-miss that must stay silent. Rules without a
   negative test are treated as unfinished, because a noisy rule gets switched off.
4. If the rule needs a new threshold, add it to `AuditConfig` and document it in
   the README configuration table.

## Conventions

- Prices and factors are `Decimal`, never `float`. Factor arithmetic is quantised
  in `adjlint.adjust` -- do not reintroduce unbounded precision.
- Standard library only. `pyproject.toml` declares `dependencies = []` and a CI
  job asserts it. If you think you need a dependency, the answer is probably a
  smaller feature.
- Never repair, impute, or drop data silently. Report it. Missing inputs become
  `AL003`-style findings; unreadable files become `DataError`.
- Output must stay deterministic: same bytes in, same report out. `Finding.key`
  exists to make sort order total.
- Keep CLI output ASCII-only so it survives Windows consoles and log aggregators.

## Before opening a pull request

```bash
pytest -q
ruff check src tests
```
