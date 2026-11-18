"""Fail CI if adjlint ever grows a runtime dependency.

Zero dependencies is the promise that makes this usable as a CI gate, so it is
enforced two ways: the declaration in ``pyproject.toml`` and, more importantly,
what the interpreter actually loads when ``adjlint`` is imported.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECLARATION = "dependencies = []"


def declared_dependencies() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies") and "=" in stripped:
            inside = stripped.split("=", 1)[1].strip().strip("[]")
            return [part.strip().strip("\"'") for part in inside.split(",") if part.strip()]
    return []


def imported_third_party() -> list[str]:
    """Modules that appeared in ``sys.modules`` purely because we imported adjlint."""
    before = set(sys.modules)
    sys.path.insert(0, str(ROOT / "src"))
    import adjlint  # noqa: F401

    stdlib = set(sys.stdlib_module_names)
    fresh = set(sys.modules) - before
    offenders = set()
    for name in fresh:
        root = name.split(".")[0]
        if root.startswith(("adjlint", "_")) or root in stdlib:
            continue
        offenders.add(root)
    return sorted(offenders)


def main() -> int:
    problems = []
    declared = declared_dependencies()
    if declared:
        problems.append(f"pyproject declares runtime dependencies: {declared}")
    if DECLARATION not in (ROOT / "pyproject.toml").read_text(encoding="utf-8"):
        problems.append(f"pyproject.toml must state `{DECLARATION}` explicitly")
    offenders = imported_third_party()
    if offenders:
        problems.append(f"importing adjlint loads non-stdlib modules: {offenders}")
    if problems:
        print("dependency contract broken:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("dependency contract holds: zero runtime dependencies, stdlib-only imports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
