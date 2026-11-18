"""Render findings as text, Markdown or JSON.

The text report is what you read in a terminal, Markdown is what you paste into
a PR comment, JSON is what CI consumes.  All three are derived from the same
finding list, so a gate and a human can never disagree about the counts.
"""

import json
from collections import Counter

from .model import Severity
from .version import VERSION

__all__ = ["FORMATS", "counts", "render", "render_json", "render_markdown", "render_text"]

_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)
FORMATS = ("text", "md", "markdown", "json")


def counts(findings) -> dict[str, int]:
    """Severity tally, always in ``error, warning, info`` order."""
    tally = Counter(f.severity.value for f in findings)
    return {s.value: tally.get(s.value, 0) for s in _ORDER}


def _meta_line(meta: dict | None, sep: str = " | ") -> str:
    meta = meta or {}
    parts = [f"adjlint {meta.get('version', VERSION)}"]
    for key in ("profile", "calendar", "symbols", "bars", "events"):
        value = meta.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return sep.join(parts)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def render_text(findings, meta: dict | None = None) -> str:
    lines = [_meta_line(meta), ""]
    if not findings:
        lines.append("clean - no findings")
        return "\n".join(lines) + "\n"
    width = max(len(f.code) + len(f.rule) + 1 for f in findings)
    for f in findings:
        stamp = f.on.isoformat() if f.on else "-"
        heading = f"{f.code} {f.rule}"
        lines.append(f"  {f.severity.value:<7} {f.symbol or '-':<8} {stamp:<10} {heading:<{width}}")
        lines.append(f"          {f.message}")
        for key, value in f.details.items():
            lines.append(f"            {key}={value}")
    tally = counts(findings)
    lines.append("")
    lines.append(", ".join(_plural(tally[s.value], s.value) for s in _ORDER if tally[s.value]))
    return "\n".join(lines) + "\n"


def render_markdown(findings, meta: dict | None = None) -> str:
    tally = counts(findings)
    lines = ["# adjlint report", "", _meta_line(meta, sep=" &middot; "), ""]
    lines.append(
        "**"
        + ", ".join(_plural(tally[s.value], s.value) for s in _ORDER)
        + "**"
    )
    lines.append("")
    if not findings:
        lines.append("No findings. The price series survives every enabled rule.")
        return "\n".join(lines) + "\n"
    lines.append("| Severity | Symbol | Date | Code | Rule | Message |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for f in findings:
        stamp = f.on.isoformat() if f.on else ""
        detail = " ".join(f"`{k}={v}`" for k, v in f.details.items())
        message = f.message if not detail else f"{f.message} ({detail})"
        cells = [f.severity.value, f.symbol or "", stamp, f.code, f"`{f.rule}`", message]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def render_json(findings, meta: dict | None = None) -> str:
    payload = {
        "adjlint": (meta or {}).get("version", VERSION),
        "meta": meta or {},
        "summary": counts(findings),
        "findings": [f.as_dict() for f in findings],
    }
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def render(findings, fmt: str = "text", meta: dict | None = None) -> str:
    if fmt == "json":
        return render_json(findings, meta)
    if fmt in ("md", "markdown"):
        return render_markdown(findings, meta)
    if fmt == "text":
        return render_text(findings, meta)
    raise ValueError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")

