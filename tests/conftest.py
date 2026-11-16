"""Shared fixtures.  Adding ``src`` to the path keeps the suite runnable from a
bare checkout, without an install step."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from adjlint import Bar, MarketProfile  # noqa: E402

#: Twelve Mon-Fri sessions in March 2026, so the weekday calendar stays quiet.
WEEK = tuple(date(2026, 3, day) for day in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17))


def flat_profile(band="0.05"):
    """A flat band with a Mon-Fri calendar and no price ladder."""
    return MarketProfile(name="test", weekend_days=(5, 6), default_band=Decimal(str(band)))


def bar(index, close, open=None, high=None, low=None, volume=1000, day=None):
    """A ``Bar`` on the i-th test session, with OHLC kept mutually consistent.

    Plain function rather than a fixture so the helpers are greppable and the
    linter can see them.
    """
    close = Decimal(str(close))
    top = max(v for v in (open, close) if v is not None)
    bottom = min(v for v in (open, close) if v is not None)
    return Bar(
        date=day if day is not None else WEEK[index],
        close=close,
        open=None if open is None else Decimal(str(open)),
        high=Decimal(str(top if high is None else high)),
        low=Decimal(str(bottom if low is None else low)),
        volume=None if volume is None else Decimal(str(volume)),
    )


def codes(findings):
    """Just the rule codes, sorted."""
    return sorted(f.code for f in findings)


def at(findings, day):
    """Findings attached to one session."""
    return [f for f in findings if f.on == day]


def by_code(findings, code):
    return [f for f in findings if f.code == code]


__all__ = ["WEEK", "at", "bar", "by_code", "codes", "flat_profile"]


