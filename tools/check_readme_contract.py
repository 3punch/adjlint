"""Fail CI if the worked example in README.md stops being true.

The README promises a specific report for the shipped example data. This script
re-reads that JSON and asserts the promise, so a rule change that shifts the
example forces a README edit in the same pull request.
"""

import json
import sys
from pathlib import Path

REPORT = Path("adjlint-report.json")
EXPECTED_SUMMARY = {"error": 2, "warning": 1, "info": 0}
EXPECTED_CODES = {"AL001", "AL002", "AL003"}


def main() -> int:
    if not REPORT.exists():
        print(f"missing {REPORT}; run the audit step first", file=sys.stderr)
        return 1
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    problems = []
    if payload["summary"] != EXPECTED_SUMMARY:
        problems.append(f"summary {payload['summary']} != {EXPECTED_SUMMARY}")
    codes = {item["code"] for item in payload["findings"]}
    if codes != EXPECTED_CODES:
        problems.append(f"codes {sorted(codes)} != {sorted(EXPECTED_CODES)}")
    if problems:
        print("README example drifted:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("Update README.md or the example data -- both ship together.", file=sys.stderr)
        return 1
    print(f"README contract holds: {payload['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
