"""CSV readers built on the standard library only.

Real exchange exports are messy in predictable ways: UTF-8 BOMs, thousands
separators, ``YYYY/MM/DD`` dates, timestamps that need to be floored to a
session in the *market's* timezone, and Persian/Arabic-Indic digits.  Every one
of these silently corrupts a numeric parse or shifts a corporate action by a
day, so they are normalised here, once, in one place.
"""

from __future__ import annotations

import csv
import itertools
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date as _date
from datetime import datetime, timedelta
from datetime import timezone as _timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

from .model import ActionKind, Bar, CorporateAction

__all__ = [
    "DataError",
    "load_actions",
    "load_adjusted",
    "load_calendar",
    "load_prices",
    "normalise_number",
    "parse_date",
    "parse_decimal",
]

WS = "\ufeff \t"
DIGIT_ALIASES = str.maketrans(
    {  # Persian and Arabic-Indic digits, and their separators, are all over
        # regional market exports.  Normalising them here keeps a bad export from
        # becoming a silently dropped row.
        **{chr(0x06F0 + i): str(i) for i in range(10)},          # ۰-۹
        **{chr(0x0660 + i): str(i) for i in range(10)},          # ٠-٩
        "\u066b": ".",   # Arabic decimal separator
        "\u066c": ",",   # Arabic thousands separator
    }
)
_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


class DataError(ValueError):
    """Raised when a CSV cannot be interpreted at all."""


def normalise_number(text: str) -> str:
    """Strip display noise so ``Decimal`` can parse what an analyst can read."""
    out = text.translate(DIGIT_ALIASES).strip(WS).replace("_", "")
    if _GROUPED.match(out):
        out = out.replace(",", "")
    out = out.removeprefix("+")
    return out


def parse_decimal(text: str | None, *, field: str = "value", row: int | None = None) -> Decimal | None:
    if text is None:
        return None
    cleaned = normalise_number(str(text))
    if cleaned in ("", "-", "--", "null", "None", "NA", "N/A"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        where = f" (row {row})" if row is not None else ""
        raise DataError(f"{field}: cannot parse {text!r} as a number{where}") from None


_DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%m-%Y", "%d/%m/%Y")


def resolve_zone(name: str):
    """Resolve an IANA zone, ``UTC``, or a fixed offset such as ``+03:30``.

    Windows ships no IANA database, so ``ZoneInfo`` there needs the separate
    ``tzdata`` wheel.  A data-quality tool must not die inside its own date
    handling, so the two forms that need no database are handled directly and a
    missing database becomes an actionable error instead of a stack trace.

    Every rejection is a ``DataError``.  Building the offset can also raise
    ``ValueError`` or ``OverflowError`` -- ``"+24000000000:00"`` overflows
    ``timedelta`` outright -- and a caller passing a nonsense zone on the command
    line deserves a usage error, not a traceback from ``datetime``.
    """
    key = name.strip()
    if key.upper() in ("UTC", "GMT", "Z"):
        return _timezone.utc
    if len(key) >= 5 and key[0] in "+-" and ":" in key:
        try:
            sign = 1 if key[0] == "+" else -1
            hours, minutes = (int(part) for part in key[1:].split(":")[:2])
            return _timezone(sign * timedelta(hours=hours, minutes=minutes))
        except (ValueError, OverflowError):
            raise DataError(
                f"timezone {name!r} is not a valid fixed offset; use a real offset "
                "such as '+03:30', which must stay within -24 to +24 hours"
            ) from None
    try:
        return ZoneInfo(key)
    except (KeyError, ValueError):
        raise DataError(
            f"timezone {name!r} is unavailable: install the 'tzdata' package "
            "(Windows ships no IANA database) or pass a fixed offset like '+03:30'"
        ) from None


def parse_date(text, timezone: str | None = None) -> _date:
    """Floor a date or timestamp to its *session*, in the market's timezone.

    A feed stamped in UTC read as local time shifts every corporate action by a
    day roughly a third of the time.  Pass the market timezone and the
    conversion happens before the date is taken.
    """
    if isinstance(text, datetime):
        moment = text
    elif isinstance(text, _date):
        return text
    else:
        raw = str(text).strip(WS).translate(DIGIT_ALIASES)
        if raw in ("", "-", "--"):
            raise DataError(f"empty date cell: {text!r}")
        candidate = raw.replace("T", " ")
        try:
            moment = datetime.fromisoformat(candidate)
        except ValueError:
            head = candidate.split(" ", 1)[0]
            moment = None
            for pattern in _DATE_PATTERNS:
                try:
                    moment = datetime.strptime(head, pattern)
                except ValueError:
                    continue
                break
            if moment is None:
                raise DataError(
                    f"unrecognised date {text!r}; expected YYYY-MM-DD or ISO-8601"
                ) from None
    if timezone and moment.tzinfo is not None:
        moment = moment.astimezone(resolve_zone(timezone))
    return moment.date()


def _header_key(name: str) -> str:
    return name.strip(WS).lower().replace(" ", "_").replace("-", "_")


#: Delimiters worth considering for an exchange export.  Restricting the set
#: keeps the sniffer from picking a character that occurs inside a note field.
_SNIFF_CANDIDATES = ",;\t"
_SAMPLE_SIZE = 8192
#: A list or iterator of lines has no notion of a byte cap, so bound the sample
#: by lines instead.  Two is enough to probe; the rest feeds the sniffer.
_SAMPLE_LINES = 64


def _never_seekable() -> bool:
    return False


def _header_of(lines: list[str], delimiter: str) -> list[str]:
    """The raw header a separator produces, before key normalisation."""
    for row in csv.reader(lines[:1], delimiter=delimiter):
        return [field or "" for field in row]
    return []


def _usable(lines: list[str], delimiter: str, required: tuple[tuple[str, ...], ...]) -> bool:
    """Would this separator actually give the caller its columns?

    ``required`` holds alias groups, mirroring what ``_pick`` accepts: a group is
    satisfied when any one of its names is present.  The first data row is
    checked as well as the header, because a header can look right while the
    rows say otherwise: ``symbol;date;close`` over bare ``FOO`` rows splits into
    three named columns and still has no date anywhere.
    """
    probe = list(csv.reader(lines[:2], delimiter=delimiter))
    if not probe or not probe[0]:
        return False
    header = [_header_key(field or "") for field in probe[0]]
    positions = [{i for i, name in enumerate(header) if name in set(group)} for group in required]
    if any(not found for found in positions):
        return False
    if len(probe) < 2:
        return True  # header only -- let the reader report the empty input
    return all(
        any(index < len(probe[1]) and probe[1][index].strip(WS) for index in found)
        for found in positions
    )


def _dialect_for(
    source: Iterable[str],
    delimiter: str | None,
    required: tuple[tuple[str, ...], ...],
    label: str,
) -> tuple[str, Iterable[str]]:
    """Return the delimiter and a line source that replays what was sampled.

    The guess is checked against the columns the caller needs instead of being
    trusted outright.  ``csv.Sniffer`` counts separators rather than reading
    them, so a ``;`` export with a comma inside one column name sniffs as a
    comma file, and the loader then blames a missing date column on a perfectly
    readable export.  The sniffer still goes first in the queue; it just has to
    earn it.  An explicit ``delimiter`` is honoured verbatim, as asked for.
    """
    if delimiter is not None:
        if len(delimiter) != 1 or delimiter in "\r\n":
            raise DataError(f"delimiter must be one character, got {delimiter!r}")
        return delimiter, source

    # Sampling starts wherever the caller left the stream and rewinds to exactly
    # that position: seek(0) would hand a preamble line back as the header.
    linewise = hasattr(source, "readline")
    rewindable = linewise and getattr(source, "seekable", _never_seekable)()
    start = source.tell() if rewindable else None
    if linewise:
        lines, sample = _sample_lines(source)
    else:
        # load_* has always taken a list or iterator of lines, not only a file
        # object, so draw the sample from the one iterator and chain it back.
        pending = iter(source)
        lines = list(itertools.islice(pending, _SAMPLE_LINES))
        sample = "".join(lines)
    if start is not None:
        source.seek(start)

    try:
        ranked = [csv.Sniffer().sniff(sample, delimiters=_SNIFF_CANDIDATES).delimiter]
    except csv.Error:
        # Sniffer refuses short and single-column samples.  A comma CSV is the
        # common case, so start there rather than refusing a readable file.
        ranked = [","]
    ranked += [candidate for candidate in _SNIFF_CANDIDATES if candidate not in ranked]

    for candidate in ranked:
        if _usable(lines, candidate, required):
            chosen = candidate
            break
    else:
        # Nothing fits, and that is not necessarily a delimiter problem.  A
        # ``date,open`` export has an unambiguous separator and a real missing
        # close column, and only the loader can say so; hijacking that with a
        # delimiter complaint would misdiagnose it.  Keep the original fallback
        # and reserve the parsed-header diagnostic for the one shape where a
        # separator demonstrably was the mistake.
        chosen = ranked[0]
        names = _header_of(lines, chosen)
        if any(any(c in name for c in _SNIFF_CANDIDATES if c != ",") for name in names):
            raise DataError(
                f"{label}: ambiguous delimiter; parsed header {names!r}; "
                "pass --delimiter to select one"
            )

    if start is not None:
        replay: Iterable[str] = source
    elif linewise:
        replay = itertools.chain(lines, source)  # a pipe cannot rewind
    else:
        replay = itertools.chain(lines, pending)
    return chosen, replay


def _sample_lines(handle: IO[str]) -> tuple[list[str], str]:
    """Read whole lines up to a cap, so a sniff never cuts a header in half."""
    lines: list[str] = []
    total = 0
    while total < _SAMPLE_SIZE:
        line = handle.readline()
        if not line:
            break
        lines.append(line)
        total += len(line)
    return lines, "".join(lines)


def _rows(
    source, delimiter: str | None = None, required: tuple[tuple[str, ...], ...] = ()
) -> tuple[list[dict[str, str]], str]:
    if isinstance(source, (str, Path)):
        try:
            handle: IO[str] = open(source, encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise DataError(f"cannot read {source}: {exc.strerror or exc}") from None
        owned, label = True, str(source)
    else:
        handle, owned, label = source, False, getattr(source, "name", "<stream>")
    try:
        chosen, lines = _dialect_for(handle, delimiter, required, label)
        reader = csv.DictReader(lines, delimiter=chosen)
        if not reader.fieldnames:
            raise DataError(f"{label}: no header row")
        reader.fieldnames = [_header_key(f or "") for f in reader.fieldnames]
        return list(reader), label
    finally:
        if owned:
            handle.close()


def _pick(row: dict[str, str], names: Iterable[str]) -> str | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


DATE_COLUMNS = ("date", "datetime", "trading_date", "session", "day", "last_dealing_date")
CLOSE_COLUMNS = ("close", "closing", "price_close", "final_price", "last_price", "adj_close")
OPEN_COLUMNS = ("open", "opening", "first_price")
HIGH_COLUMNS = ("high", "highest", "max_price")
LOW_COLUMNS = ("low", "lowest", "min_price")
VOLUME_COLUMNS = ("volume", "vol", "volume_l", "total_volume", "qty")
SYMBOL_COLUMNS = ("symbol", "ticker", "instrument", "code", "tinicode", "symbol_code")


def load_prices(
    source, timezone: str | None = None, delimiter: str | None = None
) -> dict[str, list[Bar]]:
    """Read a bar CSV into ``{symbol: [Bar, ...]}``, preserving file order.

    Duplicate sessions are kept, not collapsed -- dropping them would hide the
    very defect (AL006) that tells you the export is wrong.  ``delimiter`` skips
    dialect detection for exports the sniffer cannot classify.
    """
    rows, label = _rows(source, delimiter, (DATE_COLUMNS, CLOSE_COLUMNS))
    out: dict[str, list[Bar]] = defaultdict(list)
    for index, row in enumerate(rows, start=2):
        raw_date = _pick(row, DATE_COLUMNS)
        if raw_date is None:
            raise DataError(f"{label}:{index}: no date column (one of {', '.join(DATE_COLUMNS)})")
        close = parse_decimal(_pick(row, CLOSE_COLUMNS), field="close", row=index)
        if close is None:
            raise DataError(f"{label}:{index}: no close price")
        symbol = (_pick(row, SYMBOL_COLUMNS) or "*").strip(WS).translate(DIGIT_ALIASES)
        out[symbol].append(
            Bar(
                date=parse_date(raw_date, timezone),
                close=close,
                open=parse_decimal(_pick(row, OPEN_COLUMNS), field="open", row=index),
                high=parse_decimal(_pick(row, HIGH_COLUMNS), field="high", row=index),
                low=parse_decimal(_pick(row, LOW_COLUMNS), field="low", row=index),
                volume=parse_decimal(_pick(row, VOLUME_COLUMNS), field="volume", row=index),
                source=f"{label}:{index}",
            )
        )
    return dict(out)


KIND_COLUMNS = ("kind", "type", "event", "action", "event_type")
EFFECTIVE_COLUMNS = ("effective_date", "ex_date", "date", "base_date", "open_date", "effective")
#: The economic terms of an event, and the column names each one travels under
#: in the wild.  One table, so the accepted schema has exactly one definition.
_ACTION_FIELDS = {
    "split_ratio": ("split_ratio", "ratio", "split"),
    "bonus_ratio": ("bonus_ratio", "bonus"),
    "cash_per_share": ("cash_per_share", "dps", "dividend"),
    "subscription_price": ("subscription_price", "price"),
    "subscription_ratio": ("subscription_ratio", "coverage", "rights_ratio"),
    "previous_close": ("previous_close", "base_price", "last_close"),
}
KIND_ALIASES = {
    "split": "split",
    "stock_split": "split",
    "bonus": "bonus",
    "capital_increase": "bonus",
    "free": "bonus",
    "gratis": "bonus",
    "dividend": "dividend",
    "cash_dividend": "dividend",
    "dps": "dividend",
    "rights": "rights",
    "rights_issue": "rights",
    "subscription": "rights",
    "mixed": "mixed",
}


def load_actions(source, delimiter: str | None = None) -> list[CorporateAction]:
    """Read a corporate-action CSV.  Unknown kinds raise instead of being skipped."""
    rows, label = _rows(source, delimiter, (KIND_COLUMNS, EFFECTIVE_COLUMNS))
    actions: list[CorporateAction] = []
    for index, row in enumerate(rows, start=2):
        raw_kind = (_pick(row, KIND_COLUMNS) or "").strip(WS).lower()
        kind = KIND_ALIASES.get(raw_kind.replace(" ", "_").replace("-", "_"))
        if kind is None:
            raise DataError(
                f"{label}:{index}: unknown action kind {raw_kind!r}; "
                f"expected one of {', '.join(sorted(set(KIND_ALIASES)))}"
            )
        raw_day = _pick(row, EFFECTIVE_COLUMNS)
        if raw_day is None:
            raise DataError(f"{label}:{index}: no effective date column")
        try:
            action = CorporateAction(
                symbol=(_pick(row, SYMBOL_COLUMNS) or "*").strip(WS),
                kind=ActionKind(kind),
                effective_date=parse_date(raw_day),
                **{
                    field: parse_decimal(_pick(row, columns), field=field, row=index)
                    for field, columns in _ACTION_FIELDS.items()
                },
                note=_pick(row, ("note", "notes", "comment")),
            )
        except ValueError as exc:
            raise DataError(f"{label}:{index}: {exc}") from None
        actions.append(action)
    return actions


ADJUSTED_COLUMNS = ("adjusted_close", "adj_close", "adjusted", "close")


def load_adjusted(source, delimiter: str | None = None) -> dict[str, list[tuple[_date, Decimal]]]:
    """Vendor adjusted closes, for the AL007 cross-check."""
    rows, label = _rows(source, delimiter, (DATE_COLUMNS, ADJUSTED_COLUMNS))
    out: dict[str, list[tuple[_date, Decimal]]] = defaultdict(list)
    for index, row in enumerate(rows, start=2):
        raw_date = _pick(row, DATE_COLUMNS)
        value = parse_decimal(_pick(row, ADJUSTED_COLUMNS), field="adjusted_close", row=index)
        if raw_date is None or value is None:
            raise DataError(f"{label}:{index}: adjusted series needs a date and a price")
        out[(_pick(row, SYMBOL_COLUMNS) or "*").strip(WS)].append((parse_date(raw_date), value))
    return dict(out)


def load_calendar(source, delimiter: str | None = None) -> tuple[_date, ...]:
    """One session per line, in a ``date`` column or a bare first column."""
    # No required groups on purpose: a session list is often a single unnamed
    # column, which no separator can be inferred from and none needs to be.
    rows, label = _rows(source, delimiter)
    sessions: list[_date] = []
    for index, row in enumerate(rows, start=2):
        raw = _pick(row, DATE_COLUMNS) or (next(iter(row.values()), None) if row else None)
        if raw is None:
            raise DataError(f"{label}:{index}: no date column")
        sessions.append(parse_date(raw))
    return tuple(sessions)
