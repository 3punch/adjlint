# Security policy

## What counts

adjlint is a static analyser over files you already have. It opens no sockets,
spawns no processes, reads no environment variables, and has no runtime
dependencies, so the realistic attack surface is the *parser*.

Treated as security issues:

- a crafted CSV, XLSX-converted CSV or action table that crashes the analyser
  rather than producing a `DataError` or a finding
- pathological input that makes the run hang or exhaust memory (quadratic
  scanning is a bug here, not a performance note)
- anything in the report path that lets attacker-controlled text break out of
  the Markdown or JSON shape it is embedded in
- float contamination of a factor or price, which would silently change a
  report rather than visibly fail

Not treated as security issues: a false positive or a false negative in a rule.
Open a normal issue; a rule that fires wrongly is a correctness bug we want to
hear about, just not a disclosure.

## Reporting

Use **private vulnerability reporting** from the Security tab of the repository
rather than a public issue, so a parser crash is not published before it is
fixed. You should expect a response within a week; this is a solo-maintained
project, and saying so is more useful than a promise we cannot keep.

## Data handling

adjlint reads the files you name and writes the report you name. It never sends
anything anywhere. That is also the reason the dependency list is empty and is
intended to stay empty — see `tools/check_dependency_contract.py`, which fails CI
if it ever stops being true.
