"""``adjlint`` command line interface."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from .io import DataError, load_actions, load_adjusted, load_calendar, load_prices
from .market import MarketProfile, ProfileError, TradingCalendar, get_profile
from .model import Severity
from .report import FORMATS, render
from .rules import RULES, AuditConfig, SymbolSeries, audit_many
from .version import VERSION

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

FAIL_ON = ("error", "warning", "info", "never")


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None


def _codes(value: str) -> frozenset[str]:
    return frozenset(part.strip().upper() for part in value.split(",") if part.strip())


def _stdout_utf8() -> None:
    """Regional feeds carry non-ASCII symbols; never die on the print."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - redirected streams
            pass


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="adjlint",
        description="Lint OHLCV price data: corporate actions, band limits, adjustment factors.",
        epilog="exit codes: 0 clean, 1 findings at or above --fail-on, 2 usage or unreadable input",
    )
    root.add_argument("--version", action="version", version=f"adjlint {VERSION}")
    sub = root.add_subparsers(dest="command", metavar="command")

    run = sub.add_parser("audit", help="audit one or more instruments from CSV")
    run.add_argument("--prices", "-p", required=True, help="OHLCV CSV; optional symbol column")
    run.add_argument("--actions", "-a", help="corporate actions CSV")
    run.add_argument("--adjusted", help="vendor adjusted-close CSV, enables AL007")
    run.add_argument("--calendar", help="CSV of trading sessions; defaults to the profile rule")
    run.add_argument("--symbol", help="comma-separated symbols to audit (default: all)")
    run.add_argument(
        "--profile",
        default="generic",
        help="market profile: {}".format(", ".join(sorted(_profile_names()))),
    )
    run.add_argument("--timezone", help="IANA zone for timestamp columns (default: the profile zone)")
    run.add_argument("--tolerance", type=_decimal, default=Decimal("0.005"))
    run.add_argument("--outlier-z", type=_decimal, default=Decimal("8"), dest="outlier_z")
    run.add_argument("--outlier-min", type=int, default=20, dest="outlier_min_samples")
    run.add_argument("--stale-streak", type=int, default=5, dest="stale_streak")
    run.add_argument("--enable", type=_codes, default=None, help="run only these rule codes")
    run.add_argument("--disable", type=_codes, default=frozenset(), help="skip these rule codes")
    run.add_argument("--format", "-f", choices=FORMATS, default="text")
    run.add_argument("--fail-on", choices=FAIL_ON, default="error", dest="fail_on")
    run.add_argument("--output", "-o", help="write the report here instead of stdout")

    sub.add_parser("rules", help="print the rule catalog")
    sub.add_parser("profiles", help="print the bundled market profiles as JSON")
    return root


def _profile_names() -> list[str]:
    from .market import PROFILES

    return list(PROFILES)


def _cmd_rules() -> int:
    width = max(len(r.code) for r in RULES.values())
    sev = max(len(r.default_severity.value) for r in RULES.values())
    for item in RULES.values():
        print(f"{item.code:<{width}}  {item.default_severity.value:<{sev}}  {item.name}")
        print(f"{'':<{width}}  {' ' * sev}  {item.summary}")
    return EXIT_CLEAN


def _cmd_profiles() -> int:
    import json

    from .market import PROFILES

    print(json.dumps({p.name: p.to_dict() for p in PROFILES.values()}, indent=2))
    return EXIT_CLEAN


def _series(args, profile: MarketProfile):
    timezone = args.timezone or profile.timezone
    prices = load_prices(args.prices, timezone=timezone)
    actions = load_actions(args.actions) if args.actions else []
    adjusted = load_adjusted(args.adjusted) if args.adjusted else {}

    if args.symbol:
        wanted = {s.strip() for s in args.symbol.split(",") if s.strip()}
        missing = wanted - set(prices)
        for name in sorted(missing):
            print(f"warning: symbol {name!r} is not in {args.prices}", file=sys.stderr)
        prices = {k: v for k, v in prices.items() if k in wanted}
    if not prices:
        raise DataError(f"{args.prices}: no rows left to audit")

    known = {a.symbol for a in actions} | set(adjusted)
    for orphan in sorted(known - set(prices) - {"*"}):
        print(f"warning: events for {orphan!r} but no price rows for it", file=sys.stderr)

    return [
        SymbolSeries.of(
            symbol,
            bars,
            tuple(a for a in actions if a.symbol in (symbol, "*")),
            adjusted.get(symbol) or adjusted.get("*"),
        )
        for symbol, bars in sorted(prices.items())
    ]


def _exit_code(findings, fail_on: str) -> int:
    if fail_on == "never":
        return EXIT_CLEAN
    threshold = Severity(fail_on)
    return EXIT_FINDINGS if any(f.severity >= threshold for f in findings) else EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    args = _parser().parse_args(argv)
    if args.command == "rules":
        return _cmd_rules()
    if args.command == "profiles":
        return _cmd_profiles()
    if args.command != "audit":
        _parser().print_help()
        return EXIT_USAGE

    try:
        profile = get_profile(args.profile)
        calendar = (
            TradingCalendar.from_dates(load_calendar(args.calendar), name=args.calendar)
            if args.calendar
            else TradingCalendar.from_profile(profile)
        )
        series = _series(args, profile)
        config = AuditConfig(
            tolerance=args.tolerance,
            outlier_z=args.outlier_z,
            outlier_min_samples=args.outlier_min_samples,
            stale_streak=args.stale_streak,
            enabled=args.enable,
            disabled=args.disable,
        )
        findings = audit_many(series, profile=profile, calendar=calendar, config=config)
    except (DataError, ProfileError, ValueError) as exc:
        print(f"adjlint: {exc}", file=sys.stderr)
        return EXIT_USAGE

    meta = {
        "version": VERSION,
        "profile": profile.name,
        "calendar": calendar.name,
        "symbols": len(series),
        "bars": sum(len(s.bars) for s in series),
        "events": sum(len(s.actions) for s in series),
    }
    text = render(findings, args.format, meta)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        print(text, end="")
    return _exit_code(findings, args.fail_on)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
