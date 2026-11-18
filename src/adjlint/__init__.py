"""adjlint -- a linter for OHLCV price series.

Public API::

    from adjlint import AuditConfig, SymbolSeries, audit, get_profile

    findings = audit(SymbolSeries.of("FOO", bars, actions), profile=get_profile("tse"))
    for f in findings:
        print(f.severity, f.on, f.code, f.message)

The library has no runtime dependencies and never imputes a missing value: an
event whose reference price cannot be derived is labelled ``AL003 unclassified-gap``
and left in the report for a human to resolve.
"""

from .adjust import (
    ResolvedAction,
    adjusted_series,
    adjustment_factor,
    reference_price,
    resolve_actions,
)
from .io import DataError, load_actions, load_adjusted, load_calendar, load_prices
from .market import GENERIC, TSE, MarketProfile, ProfileError, TradingCalendar, get_profile
from .model import ActionKind, Bar, CorporateAction, Finding, Severity
from .report import render
from .rules import RULES, AuditConfig, Rule, SymbolSeries, audit, audit_many
from .version import VERSION

__version__ = VERSION

__all__ = [
    "GENERIC",
    "RULES",
    "TSE",
    "VERSION",
    "ActionKind",
    "AuditConfig",
    "Bar",
    "CorporateAction",
    "DataError",
    "Finding",
    "MarketProfile",
    "ProfileError",
    "ResolvedAction",
    "Rule",
    "Severity",
    "SymbolSeries",
    "TradingCalendar",
    "adjusted_series",
    "adjustment_factor",
    "audit",
    "audit_many",
    "get_profile",
    "load_actions",
    "load_adjusted",
    "load_calendar",
    "load_prices",
    "reference_price",
    "render",
    "resolve_actions",
]
