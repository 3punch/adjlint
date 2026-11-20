"""Market structure: price-limit bands, tick sizes and trading calendars.

Band limits are *market policy* values that change over time.  The presets
below are convenient starting points, not authoritative quotations -- verify
them against the current exchange directive before using ``adjlint`` as a
production gate.  Everything here is overridable from the CLI or a config dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

__all__ = [
    "GENERIC",
    "PROFILES",
    "TSE",
    "MarketProfile",
    "ProfileError",
    "TradingCalendar",
    "get_profile",
    "load_profile",
]

#: ``(inclusive upper bound of the previous close, band limit)``.
#: The first matching rung wins; a ``None`` bound means "everything above".
BandRung = tuple[Decimal | None, Decimal]


class ProfileError(ValueError):
    """Raised for an unusable market profile or calendar definition."""


@dataclass(frozen=True, slots=True)
class MarketProfile:
    """Exchange microstructure rules relevant to price-series integrity."""

    name: str
    timezone: str = "UTC"
    #: ``monday=0 .. sunday=6``
    weekend_days: tuple[int, ...] = (5, 6)
    band_ladder: tuple[BandRung, ...] = ()
    default_band: Decimal = Decimal("0.20")
    price_tick: Decimal | None = None
    currency: str = ""
    description: str = ""

    def band_limit(self, previous_close: Decimal) -> Decimal:
        """Daily price-limit band as a fraction, e.g. ``0.03`` for +/-3%."""
        for bound, band in self.band_ladder:
            if bound is None or previous_close <= bound:
                return band
        return self.default_band

    def validate(self) -> None:
        if self.default_band <= 0 or self.default_band >= 1:
            raise ProfileError(f"{self.name}: default_band must be in (0, 1)")
        for day in self.weekend_days:
            if not 0 <= day <= 6:
                raise ProfileError(
                    f"{self.name}: weekend_days entry {day} must be 0-6 (monday=0 .. sunday=6)"
                )
        prev_bound: Decimal | None = None
        last = len(self.band_ladder) - 1
        for position, (bound, band) in enumerate(self.band_ladder):
            rung = f"{self.name}: band_ladder[{position}]"
            if band <= 0 or band >= 1:
                raise ProfileError(f"{rung}: band limit {band} out of range (0, 1)")
            if bound is None:
                if position != last:
                    raise ProfileError(f"{rung}: an open-ended bound must be the last rung")
                continue
            if prev_bound is not None and bound <= prev_bound:
                raise ProfileError(f"{rung}: bounds must ascend ({bound} <= {prev_bound})")
            prev_bound = bound

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "timezone": self.timezone,
            "weekend_days": list(self.weekend_days),
            "default_band": str(self.default_band),
            "band_ladder": [
                [None if b is None else str(b), str(v)] for b, v in self.band_ladder
            ],
            "price_tick": None if self.price_tick is None else str(self.price_tick),
            "currency": self.currency,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data) -> MarketProfile:
        """Inverse of :meth:`to_dict`.  Only ``name`` is required.

        Decimals must be JSON *strings*: a JSON number arrives as a float, and
        ``Decimal(0.05)`` is not ``0.05``, so numbers are rejected rather than
        silently rounded.  ``null`` means "absent" for ``price_tick`` and for a
        rung's bound, never zero.
        """
        if not isinstance(data, dict):
            raise ProfileError(f"a profile must be a JSON object, got {_shown(data)}")
        unknown = sorted(set(data) - {f.name for f in fields(cls)})
        if unknown:
            raise ProfileError(f"unknown field(s): {', '.join(unknown)}")
        if "name" not in data:
            raise ProfileError("missing required field 'name'")
        values: dict = {}
        for key in ("name", "timezone", "currency", "description"):
            if key in data:
                if not isinstance(data[key], str):
                    raise ProfileError(f"{key} must be a string, got {_shown(data[key])}")
                values[key] = data[key]
        if "weekend_days" in data:
            days = data["weekend_days"]
            # bool is an int subclass, so check the exact type.
            if not isinstance(days, list) or any(type(d) is not int for d in days):
                raise ProfileError(
                    f"weekend_days must be a list of integers 0-6 (monday=0), got {_shown(days)}"
                )
            values["weekend_days"] = tuple(days)
        if "default_band" in data:
            values["default_band"] = _decimal("default_band", data["default_band"])
        if "price_tick" in data:
            values["price_tick"] = _decimal("price_tick", data["price_tick"], optional=True)
        if "band_ladder" in data:
            ladder = data["band_ladder"]
            if not isinstance(ladder, list):
                raise ProfileError(f"band_ladder must be a list of rungs, got {_shown(ladder)}")
            rungs = []
            for position, rung in enumerate(ladder):
                where = f"band_ladder[{position}]"
                if not isinstance(rung, list) or len(rung) != 2:
                    raise ProfileError(
                        f'{where} must be a [bound, band] pair such as ["250", "0.05"], '
                        f"got {_shown(rung)}"
                    )
                rungs.append(
                    (
                        _decimal(f"{where} bound", rung[0], optional=True),
                        _decimal(f"{where} band", rung[1]),
                    )
                )
            values["band_ladder"] = tuple(rungs)
        return cls(**values)


def _shown(value) -> str:
    return json.dumps(value)


def _decimal(where: str, value, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ProfileError(
            f'{where} must be a decimal written as a string, such as "0.05", got {_shown(value)}'
        )
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ProfileError(f"{where}: {value!r} is not a decimal number") from None
    if not number.is_finite():
        raise ProfileError(f"{where}: {value!r} is not a finite number")
    return number


#: A market with no published ladder: one flat +/-20% band.  Sensible default
#: for crypto, US equities on a daily frame, or any feed you know nothing about.
GENERIC = MarketProfile(name="generic", description="Flat 20% band, Mon-Fri calendar.")

#: Tehran Stock Exchange.  The ladder is an illustrative example of the
#: price-band *shape* (tighter bands at higher price levels); the numbers must
#: be re-checked against the current directive.  Friday is the weekend.
TSE = MarketProfile(
    name="tse",
    timezone="Asia/Tehran",
    weekend_days=(4,),
    band_ladder=(
        (Decimal("5000"), Decimal("0.05")),
        (Decimal("20000"), Decimal("0.03")),
        (None, Decimal("0.02")),
    ),
    default_band=Decimal("0.02"),
    price_tick=Decimal("10"),
    currency="IRR",
    description="TSE example profile -- confirm band limits against the current directive.",
)

PROFILES: dict[str, MarketProfile] = {p.name: p for p in (GENERIC, TSE)}


def get_profile(name: str) -> MarketProfile:
    try:
        return PROFILES[name.strip().lower()]
    except KeyError:
        raise ProfileError(
            f"unknown market profile {name!r}; available: {', '.join(sorted(PROFILES))}"
        ) from None


def load_profile(path: str) -> MarketProfile:
    """Read and validate a JSON profile; every failure is a ``ProfileError`` naming ``path``.

    The result is deliberately not added to ``PROFILES``: the bundled catalog
    must not depend on one run's arguments.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            profile = MarketProfile.from_dict(json.load(handle))
        profile.validate()
    except OSError as exc:
        raise ProfileError(f"{path}: cannot read profile file: {exc.strerror or exc}") from None
    except ValueError as exc:  # ProfileError, JSONDecodeError and UnicodeDecodeError
        raise ProfileError(f"{path}: {exc}") from None
    return profile


class TradingCalendar:
    """Minimal session calendar: weekday rule plus an explicit holiday set.

    Feeds for markets that stop trading for reasons the calendar does not know
    about (halted symbols, data outages) are exactly what AL006 exists to catch,
    so holidays here are deliberately explicit rather than guessed.
    """

    def __init__(
        self,
        weekend_days: tuple[int, ...] = (5, 6),
        holidays: tuple[date, ...] = (),
        name: str = "custom",
        explicit: bool = False,
    ) -> None:
        self.weekend_days = frozenset(weekend_days)
        self.holidays = frozenset(holidays)
        self.name = name
        #: True when the session set was enumerated rather than guessed from a
        #: weekday rule.  Only then is "a session with no bar" a real finding:
        #: every market has ad-hoc halts and holidays a weekday rule cannot know.
        self.explicit = explicit

    @classmethod
    def from_profile(cls, profile: MarketProfile) -> TradingCalendar:
        return cls(weekend_days=profile.weekend_days, name=profile.name)

    @classmethod
    def from_dates(cls, sessions, name: str = "explicit") -> TradingCalendar:
        days = frozenset(sessions)
        if not days:
            raise ProfileError("cannot build a calendar from zero sessions")
        # Everything outside the supplied session set is a non-session day.
        start, end = min(days), max(days)
        gap: list[date] = []
        cursor = start
        while cursor <= end:
            if cursor not in days:
                gap.append(cursor)
            cursor += timedelta(days=1)
        return cls(weekend_days=(), holidays=tuple(gap), name=name, explicit=True)

    def is_session(self, day: date) -> bool:
        return day.weekday() not in self.weekend_days and day not in self.holidays

    def sessions_between(self, start: date, end: date) -> list[date]:
        cursor, out = start, []
        while cursor <= end:
            if self.is_session(cursor):
                out.append(cursor)
            cursor += timedelta(days=1)
        return out

    def missing_sessions(self, observed, start: date | None = None, end: date | None = None):
        """Calendar sessions with no bar between ``start`` and ``end``."""
        seen = set(observed)
        first = start or (min(seen) if seen else None)
        last = end or (max(seen) if seen else None)
        if first is None or last is None:
            return []
        return [d for d in self.sessions_between(first, last) if d not in seen]
