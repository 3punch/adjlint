"""The lint rules.

Every rule is a pure function over an :class:`AuditContext` and yields
:class:`~adjlint.model.Finding` objects, so the whole catalog can be run in CI
with no I/O, no network and no plugin host.
"""

from __future__ import annotations

import itertools
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .adjust import ResolvedAction, adjusted_series, resolve_actions
from .market import GENERIC, MarketProfile, TradingCalendar
from .model import Bar, CorporateAction, Finding, Severity

__all__ = ["RULES", "AuditConfig", "AuditContext", "Rule", "SymbolSeries", "audit"]

ZERO = Decimal(0)
ONE = Decimal(1)
_DP = Decimal("0.000001")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Relative change ``(n/d) - 1``, quantised for reproducible comparisons."""
    return ((numerator / denominator) - ONE).quantize(_DP)


def _median(values: Sequence[Decimal]) -> Decimal:
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / Decimal(2)


@dataclass(frozen=True, slots=True)
class AuditConfig:
    """Thresholds.  Everything is a ``Decimal``/``int`` so it round-trips to JSON."""

    #: Relative slack on "the expected move happened" comparisons.
    tolerance: Decimal = Decimal("0.005")
    #: Robust z-score (median/MAD) beyond which a daily return is an outlier.
    outlier_z: Decimal = Decimal("8")
    outlier_min_samples: int = 20
    #: Consecutive identical closes that count as a frozen feed.
    stale_streak: int = 5
    #: Rule codes to run; ``None`` = all of them.
    enabled: frozenset[str] | None = None
    #: Suppress noisy rules by code.
    disabled: frozenset[str] = frozenset()

    def is_active(self, code: str) -> bool:
        if code in self.disabled:
            return False
        return self.enabled is None or code in self.enabled


@dataclass(frozen=True, slots=True)
class SymbolSeries:
    """One instrument: raw bars, booked corporate actions, optional vendor series."""

    symbol: str
    bars: tuple[Bar, ...]
    actions: tuple[CorporateAction, ...] = ()
    #: Vendor-supplied adjusted closes, ``(date, adjusted_close)``, for AL007.
    vendor_adjusted: tuple[tuple[date, Decimal], ...] | None = None

    @staticmethod
    def of(symbol, bars, actions=(), vendor_adjusted=None) -> SymbolSeries:
        return SymbolSeries(
            symbol=symbol,
            bars=tuple(bars),
            actions=tuple(actions),
            vendor_adjusted=None if vendor_adjusted is None else tuple(vendor_adjusted),
        )


@dataclass(slots=True)
class AuditContext:
    symbol: str
    bars: list[Bar]
    actions: list[CorporateAction]
    resolved: list[ResolvedAction]
    profile: MarketProfile
    calendar: TradingCalendar
    config: AuditConfig
    vendor_adjusted: dict[date, Decimal] = field(default_factory=dict)

    def finding(self, code: str, severity: Severity, message: str, on=None, **details) -> Finding:
        rule = RULES[code]
        return Finding(
            code=code,
            rule=rule.name,
            severity=severity,
            message=message,
            symbol=self.symbol,
            on=on,
            details={k: _fmt(v) for k, v in details.items() if v is not None},
        )

    @property
    def effective_on(self) -> dict[date, list[ResolvedAction]]:
        grouped: dict[date, list[ResolvedAction]] = {}
        for item in self.resolved:
            grouped.setdefault(item.action.effective_date, []).append(item)
        return grouped


# --------------------------------------------------------------------------- #
# rule registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Rule:
    code: str
    name: str
    summary: str
    default_severity: Severity


RULES: dict[str, Rule] = {}
_REGISTRY: list[tuple[str, Callable[[AuditContext], Iterable[Finding]]]] = []


def rule(code: str, name: str, summary: str, severity: Severity):
    def decorate(func):
        RULES[code] = Rule(code, name, summary, severity)
        _REGISTRY.append((code, func))
        return func

    return decorate


def _ordered(ctx: AuditContext) -> list[Bar]:
    return sorted(ctx.bars, key=lambda b: b.date)


def _pairs(ctx: AuditContext) -> Iterator[tuple[Bar, Bar]]:
    ordered = _ordered(ctx)
    yield from itertools.pairwise(ordered)


def _base_price(factors: Iterable[Decimal]) -> Decimal:
    base = ONE
    for factor in factors:
        base *= factor
    return base


def _pct(value: Decimal) -> str:
    return f"{value * 100:.2f}%"


def _fmt(value) -> str:
    """Render a detail value for a report: fixed-point, no trailing-zero noise."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


@rule("AL001", "band-limit-breach", "Daily move exceeds the price band with no booked event.", Severity.ERROR)
def _r_band_limit(ctx: AuditContext) -> Iterator[Finding]:
    grouped = ctx.effective_on
    for previous, bar in _pairs(ctx):
        if previous.close <= 0:
            continue
        items = grouped.get(bar.date, ())
        base = previous.close * _base_price(i.factor for i in items if i.usable)
        band = ctx.profile.band_limit(previous.close)
        move = _ratio(bar.close, base)
        if abs(move) - band > ctx.config.tolerance:
            yield ctx.finding(
                "AL001",
                Severity.ERROR,
                f"daily move {_pct(move)} is outside the {_pct(band)} band"
                f"{' with no corporate event booked' if not items else ''}",
                on=bar.date,
                prev_close=previous.close,
                expected_base=base.quantize(_DP),
                band=_pct(band),
                observed=_pct(move),
            )


@rule("AL002", "action-not-applied", "A booked event never shows up in the price series.", Severity.ERROR)
def _r_action_not_applied(ctx: AuditContext) -> Iterator[Finding]:
    tolerance = ctx.config.tolerance
    for previous, bar in _pairs(ctx):
        items = [i for i in ctx.effective_on.get(bar.date, ()) if i.usable]
        if not items or previous.close <= 0:
            continue
        expected = _base_price(i.factor for i in items) - ONE
        if abs(expected) <= tolerance:
            continue
        observed = _ratio(bar.open if bar.open is not None else bar.close, previous.close)
        if abs(observed) <= abs(expected) / 4:
            yield ctx.finding(
                "AL002",
                Severity.ERROR,
                f"{len(items)} booked event(s) imply a {_pct(expected)} reset but the "
                f"session opened {_pct(observed)} off the previous close",
                on=bar.date,
                expected=_pct(expected),
                observed=_pct(observed),
                price_field="open" if bar.open is not None else "close",
            )


@rule(
    "AL003",
    "unclassified-gap",
    "Event on record but no reference price -- labelled, never imputed.",
    Severity.WARNING,
)
def _r_unclassified_gap(ctx: AuditContext) -> Iterator[Finding]:
    for item in ctx.resolved:
        if item.usable:
            continue
        action = item.action
        yield ctx.finding(
            "AL003",
            Severity.WARNING,
            "no reference price: the series has no close before the effective date and "
            "the action declares no previous_close -- the event is labelled rather than guessed",
            on=action.effective_date,
            kind=action.kind.value,
            fix="extend the price series backwards, or set previous_close on the action",
        )


@rule("AL004", "duplicate-event", "The same corporate event booked twice on one date.", Severity.ERROR)
def _r_duplicate_event(ctx: AuditContext) -> Iterator[Finding]:
    grouped: dict[date, list[CorporateAction]] = {}
    for action in ctx.actions:
        grouped.setdefault(action.effective_date, []).append(action)
    for day, items in sorted(grouped.items()):
        if len(items) < 2:
            continue
        keys = [(i.kind, i.split_ratio, i.bonus_ratio, i.cash_per_share,
                 i.subscription_price, i.subscription_ratio) for i in items]
        if len(set(keys)) == 1:
            yield ctx.finding(
                "AL004",
                Severity.ERROR,
                f"{len(items)} identical {items[0].kind.value} events on one date -- "
                "the adjustment factor would be applied twice",
                on=day,
            )
        else:
            yield ctx.finding(
                "AL004",
                Severity.WARNING,
                f"{len(items)} distinct events share one effective date ({', '.join(i.kind.value for i in items)})",
                on=day,
            )


@rule("AL005", "outlier-return", "Robust (median/MAD) outlier among quiet daily returns.", Severity.WARNING)
def _r_outlier_return(ctx: AuditContext) -> Iterator[Finding]:
    grouped = ctx.effective_on
    pairs = [
        (p, b)
        for p, b in _pairs(ctx)
        if p.close > 0 and not any(i.usable for i in grouped.get(b.date, ()))
    ]
    if len(pairs) < ctx.config.outlier_min_samples:
        return
    returns = [_ratio(b.close, p.close) for p, b in pairs]
    med = _median(returns)
    mad = _median([abs(r - med) for r in returns])
    if mad <= 0:
        return
    for (_previous, bar), value in zip(pairs, returns, strict=False):
        score = (Decimal("0.6745") * (value - med) / mad).quantize(_DP)
        if abs(score) > ctx.config.outlier_z:
            yield ctx.finding(
                "AL005",
                Severity.WARNING,
                f"daily return {_pct(value)} is {abs(score):.1f} robust sigma from the "
                f"median {_pct(med)} with nothing booked",
                on=bar.date,
                robust_z=score,
                median_return=_pct(med),
            )


@rule("AL006", "calendar-misalignment", "Bars duplicated, unsorted, off-calendar, or sessions missing.", Severity.ERROR)
def _r_calendar(ctx: AuditContext) -> Iterator[Finding]:
    seen: set[date] = set()
    for bar in ctx.bars:
        if bar.date in seen:
            yield ctx.finding("AL006", Severity.ERROR, "duplicate bar for one session", on=bar.date)
        seen.add(bar.date)
    if ctx.bars != _ordered(ctx):
        yield ctx.finding(
            "AL006",
            Severity.ERROR,
            f"bars arrive out of order ({len(ctx.bars)} rows); the feed is not monotonic in date",
            on=None,
        )
    for bar in _ordered(ctx):
        if not ctx.calendar.is_session(bar.date):
            yield ctx.finding(
                "AL006",
                Severity.ERROR,
                "bar on a non-session day for this calendar",
                on=bar.date,
                calendar=ctx.calendar.name,
            )
    if ctx.calendar.explicit:
        for missing in ctx.calendar.missing_sessions(seen):
            yield ctx.finding(
                "AL006",
                Severity.WARNING,
                "session in the supplied calendar with no bar",
                on=missing,
                calendar=ctx.calendar.name,
            )


@rule("AL007", "adjustment-mismatch", "Vendor adjusted closes disagree with recomputed ones.", Severity.ERROR)
def _r_adjustment_mismatch(ctx: AuditContext) -> Iterator[Finding]:
    if not ctx.vendor_adjusted:
        return
    for bar, factor, adjusted in adjusted_series(ctx.bars, ctx.resolved):
        supplied = ctx.vendor_adjusted.get(bar.date)
        if supplied is None or adjusted <= 0:
            continue
        drift = _ratio(supplied, adjusted)
        if abs(drift) > ctx.config.tolerance:
            yield ctx.finding(
                "AL007",
                Severity.ERROR,
                f"vendor adjusted close is {_pct(drift)} away from the recomputed value",
                on=bar.date,
                recomputed=adjusted,
                supplied=supplied,
                factor=factor,
            )


@rule("AL008", "ohlc-inconsistency", "Non-positive prices, inverted ranges, negative volume.", Severity.ERROR)
def _r_ohlc(ctx: AuditContext) -> Iterator[Finding]:
    for bar in ctx.bars:
        named = {"close": bar.close, "open": bar.open, "high": bar.high, "low": bar.low}
        for label, value in named.items():
            if value is not None and value <= 0:
                yield ctx.finding(
                    "AL008", Severity.ERROR, f"non-positive {label} price", on=bar.date, **{label: value}
                )
        if bar.high is not None and bar.low is not None and bar.high < bar.low:
            yield ctx.finding(
                "AL008", Severity.ERROR, "high below low", on=bar.date, high=bar.high, low=bar.low
            )
        if bar.has_ohlc:
            top = max(v for v in (bar.open, bar.high, bar.close) if v is not None)
            bottom = min(v for v in (bar.open, bar.low, bar.close) if v is not None)
            if bar.high < top or bar.low > bottom:
                yield ctx.finding(
                    "AL008",
                    Severity.ERROR,
                    "range does not contain the session open/close",
                    on=bar.date,
                    high=bar.high,
                    low=bar.low,
                    open=bar.open,
                    close=bar.close,
                )
        if bar.volume is not None and bar.volume < 0:
            yield ctx.finding("AL008", Severity.ERROR, "negative volume", on=bar.date, volume=bar.volume)


@rule(
    "AL009",
    "frozen-price-streak",
    "Identical closes across many sessions -- feed stopped updating.",
    Severity.WARNING,
)
def _r_frozen(ctx: AuditContext) -> Iterator[Finding]:
    ordered = _ordered(ctx)
    if not ordered:
        return
    run_start = 0
    for index in range(1, len(ordered) + 1):
        same_tail = index < len(ordered) and ordered[index].close == ordered[run_start].close
        if same_tail:
            continue
        length = index - run_start
        if length >= ctx.config.stale_streak:
            first, last = ordered[run_start], ordered[index - 1]
            yield ctx.finding(
                "AL009",
                Severity.WARNING,
                f"close frozen at {first.close} for {length} sessions",
                on=last.date,
                frozen_from=first.date,
                sessions=length,
            )
        run_start = index


@rule("AL010", "base-price-disagreement", "Declared base price disagrees with the series close.", Severity.WARNING)
def _r_base_disagreement(ctx: AuditContext) -> Iterator[Finding]:
    ordered = _ordered(ctx)
    dates = [b.date for b in ordered]
    for action in ctx.actions:
        if action.previous_close is None:
            continue
        idx = bisect_left(dates, action.effective_date) - 1
        if idx < 0:
            continue
        series_close = ordered[idx].close
        drift = _ratio(action.previous_close, series_close)
        if abs(drift) > ctx.config.tolerance:
            yield ctx.finding(
                "AL010",
                Severity.WARNING,
                f"declared base price is {_pct(drift)} from the close of {dates[idx]} -- "
                "wrong ex-date, or the event was booked against another session",
                on=action.effective_date,
                declared=action.previous_close,
                series_close=series_close,
            )


def audit(
    series: SymbolSeries,
    profile: MarketProfile = GENERIC,
    calendar: TradingCalendar | None = None,
    config: AuditConfig | None = None,
) -> list[Finding]:
    """Run every enabled rule over one instrument and return sorted findings."""
    config = config or AuditConfig()
    ctx = AuditContext(
        symbol=series.symbol,
        bars=list(series.bars),
        actions=list(series.actions),
        resolved=resolve_actions(series.bars, series.actions),
        profile=profile,
        calendar=calendar or TradingCalendar.from_profile(profile),
        config=config,
        vendor_adjusted=dict(series.vendor_adjusted or ()),
    )
    findings: list[Finding] = []
    for code, func in _REGISTRY:
        if config.is_active(code):
            findings.extend(func(ctx))
    return sorted(findings)


def audit_many(
    series: Iterable[SymbolSeries],
    profile: MarketProfile = GENERIC,
    calendar: TradingCalendar | None = None,
    config: AuditConfig | None = None,
) -> list[Finding]:
    out: list[Finding] = []
    for item in series:
        out.extend(audit(item, profile=profile, calendar=calendar, config=config))
    return sorted(out)
