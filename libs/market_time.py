"""Canonical Indian market timezone helpers.

Trading/session/calendar decisions use Asia/Kolkata explicitly. Persistence,
event, audit, and cross-service timestamps remain timezone-aware UTC.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def ist_now() -> datetime:
    """Return the current India-market wall clock as an aware datetime."""
    return datetime.now(IST)


def market_date(value: datetime | None = None) -> date:
    """Return the India-market calendar date for an instant."""
    instant = value or datetime.now(UTC)
    return as_ist(instant).date()


def ist_today() -> date:
    """Return the current India-market calendar date."""
    return market_date()


def as_ist(value: datetime) -> datetime:
    """Convert an aware datetime to IST; naive values are exchange-local IST."""
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def exchange_datetime_to_utc(value: datetime) -> datetime:
    """Normalize an exchange timestamp to aware UTC.

    Broker/exchange timestamps without an explicit timezone are interpreted as
    Asia/Kolkata wall-clock values, never as host-local or UTC wall clock.
    """
    return as_ist(value).astimezone(UTC)
