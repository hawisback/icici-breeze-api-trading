"""Timezone policy regression tests for India-market trading paths."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from libs.market_time import IST, market_date
from services.broker_gateway.infrastructure.icici.datetime_mapper import (
    to_breeze_iso,
)
from services.broker_gateway.zerodha_kite_adapter import _parse_datetime


ROOT = Path(__file__).resolve().parents[1]


def test_market_date_rolls_over_at_midnight_ist_not_midnight_utc():
    before_midnight_ist = datetime(2026, 9, 24, 18, 29, tzinfo=timezone.utc)
    midnight_ist = datetime(2026, 9, 24, 18, 30, tzinfo=timezone.utc)

    assert market_date(before_midnight_ist).isoformat() == "2026-09-24"
    assert market_date(midnight_ist).isoformat() == "2026-09-25"


def test_naive_kite_broker_timestamp_is_interpreted_as_ist():
    parsed = _parse_datetime(datetime(2026, 9, 25, 9, 15))

    assert parsed == datetime(2026, 9, 25, 3, 45, tzinfo=timezone.utc)


def test_naive_breeze_request_datetime_is_interpreted_as_ist():
    assert (
        to_breeze_iso(datetime(2026, 9, 25, 9, 15))
        == "2026-09-25T03:45:00.000Z"
    )


def test_runtime_trading_modules_use_canonical_market_timezone():
    roots = [
        ROOT / "services" / "broker_gateway",
        ROOT / "services" / "instrument",
        ROOT / "services" / "market_data",
        ROOT / "services" / "option_chain",
        ROOT / "services" / "strategy",
    ]
    extra_files = [ROOT / "services" / "historical" / "service.py"]
    files = [
        path
        for directory in roots
        for path in directory.rglob("*.py")
        if "migrations" not in path.parts
    ] + extra_files

    violations: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        if "date.today(" in text:
            violations.append(f"{rel}: date.today()")
        if re.search(r"datetime\.now\(\s*\)", text):
            violations.append(f"{rel}: naive datetime.now()")
        if re.search(
            r"timezone\(\s*timedelta\(\s*hours\s*=\s*5",
            text,
        ):
            violations.append(f"{rel}: hand-built UTC+05:30 timezone")
        if 'ZoneInfo("Asia/Kolkata")' in text:
            violations.append(f"{rel}: duplicate Asia/Kolkata ZoneInfo")

    assert violations == []


def test_frontend_does_not_render_trading_times_in_browser_local_timezone():
    frontend = ROOT / "frontend" / "trading-ui"
    violations: list[str] = []
    for path in frontend.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        if "toLocaleTimeString(" in text:
            violations.append(str(path.relative_to(ROOT)))

    assert violations == []


def test_canonical_ist_zone_is_asia_kolkata():
    assert getattr(IST, "key", None) == "Asia/Kolkata"
