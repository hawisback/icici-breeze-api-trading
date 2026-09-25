"""Datetime and Timezone Mapper for ICICI Breeze API.

Converts between UTC timezone-aware datetimes and Indian market exchange ISO strings.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from libs.market_time import IST, exchange_datetime_to_utc


def to_breeze_iso(dt: datetime) -> str:
    """Format datetime to Breeze-compliant ISO format (YYYY-MM-DDTHH:MM:SS.000Z)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def to_breeze_date_str(d: date | datetime) -> str:
    """Format date to Breeze-compliant date format (YYYY-MM-DD)."""
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        ist_dt = d.astimezone(IST)
        return ist_dt.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def parse_breeze_datetime(dt_str: str) -> datetime:
    """Parse a datetime string from Breeze into a UTC timezone-aware datetime."""
    # Breeze returns strings like '2026-09-16 15:30:00' or ISO strings
    dt_str = dt_str.strip()
    try:
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        # Assumed IST exchange time
        parsed = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return exchange_datetime_to_utc(parsed)
    except Exception:
        # Fallback to current UTC if unparseable
        return datetime.now(timezone.utc)

