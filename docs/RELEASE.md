# Cutting a release

Publishing is a GitHub release plus one setting on PyPI. There is no token to
keep, rotate or leak.

## One-time setup (five minutes, do it once)

1. **Claim the name.** Check <https://pypi.org/project/adjlint/> is yours. If the
   project is unregistered, the first upload claims it.
2. Do **not** create an API token. Trusted Publishing exists precisely so that no
   long-lived credential is stored in GitHub.
3. On PyPI, open your project → **Publishing** → **Add publisher** → **GitHub Action**
   and enter exactly:

   | field | value |
   | --- | --- |
   | Repository owner | `3punch` |
   | Repository name | `adjlint` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The workflow file name and the environment name must match `.github/workflows/publish.yml`
   character for character; PyPI matches on those, not on the tag.
4. If the repository uses environments, add a `pypi` environment in
   **Settings → Environments** so the job can reference it.

The reason those four strings matter: PyPI is handed the run's OIDC identity, and it
will only mint an upload credential when owner, repo, workflow file and environment
all match what you registered. A renamed workflow file produces an opaque
"Token valid but no matching publisher" error at the worst possible moment.

## Every release

```bash
# 1. bump the single source of truth
#    src/adjlint/version.py  ->  VERSION = "0.2.0"
#    pyproject.toml          ->  version = "0.2.0"

# 2. move the unreleased notes in CHANGELOG.md under a "## 0.2.0 - YYYY-MM-DD" head

# 3. the whole gate, exactly as CI runs it
ruff check src tests tools
pytest
adjlint audit -p examples/prices.csv -a examples/actions.csv \
  --profile tse --format json --fail-on never -o adjlint-report.json
python tools/check_readme_contract.py
python tools/check_dependency_contract.py

# 4. tag, push, release
git commit -am "adjlint 0.2.0"
git tag -a v0.2.0 -m "adjlint 0.2.0"
git push origin main --follow-tags
gh release create v0.2.0 --generate-notes
```

Publishing happens when the release is **published**, so a draft release is the
safe way to rehearse. `.github/workflows/publish.yml` builds the sdist and wheel,
runs `twine check` (which fails the job if the long description would not render
on PyPI), and uploads.

## Versioning rule specific to this project

A rule that starts or stops firing is a breaking change, even when no function
signature moved: it changes what passes in someone's CI. So:

- **major** — a rule code is added, removed, re-scoped or re-levelled; the factor
  formula changes; the shape of a finding changes
- **minor** — new options, new reporters, new profiles, new input aliases
- **patch** — message wording, docs, crashes, parsing gaps that only ever make
  the report *quieter about real data*
