"""Kite option OI delta is relative to the first verified session observation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.market_time import IST
from services.option_chain.oi_baselines import KiteSessionOIBaselines


def _item(value: int, at: datetime, instrument: str = "INST-NIFTY-2026-10-13-22500-CE"):
    return {
        "instrument_id": instrument,
        "open_interest": value, "market_timestamp": at.isoformat(),
    }


@pytest.mark.asyncio
async def test_baseline_persists_across_restarts_and_yields_signed_changes(tmp_path):
    observed = datetime(2026, 10, 9, 10, 0, tzinfo=IST)
    chain = {"strikes": [{"call": _item(10000, observed), "put": None}]}
    db = tmp_path / "session_oi.db"
    store = KiteSessionOIBaselines(db)
    await store.enrich(chain, now=observed + timedelta(seconds=2))
    first = chain["strikes"][0]["call"]
    assert first["oi_change"] is None  # no comparison before first tick
    assert first["oi_change_basis"] == "FIRST_OBSERVED_SESSION"
    assert first["oi_baseline_at"] == observed.isoformat()

    next_quote = observed + timedelta(seconds=30)
    followup = {"strikes": [
        {"call": _item(9700, next_quote), "put": _item(5000, next_quote,
         "INST-NIFTY-2026-10-13-22500-PE")}
    ]}
    await KiteSessionOIBaselines(db).enrich(followup, now=next_quote + timedelta(seconds=2))
    assert followup["strikes"][0]["call"]["oi_change"] == -300
    assert followup["strikes"][0]["put"]["oi_change"] is None

    final = {"strikes": [{"call": _item(10400, next_quote + timedelta(seconds=12))}]}
    await KiteSessionOIBaselines(db).enrich(final, now=next_quote + timedelta(seconds=14))
    assert final["strikes"][0]["call"]["oi_change"] == 400


@pytest.mark.asyncio
async def test_stale_missing_and_pre_market_quotes_do_not_seed_a_baseline(tmp_path):
    at = datetime(2026, 10, 9, 10, 0, tzinfo=IST)
    store = KiteSessionOIBaselines(tmp_path / "safe.db")
    stale = {"strikes": [{"call": _item(100, at - timedelta(minutes=5))}]}
    await store.enrich(stale, now=at)
    assert stale["strikes"][0]["call"]["oi_change"] is None
    assert not (tmp_path / "safe.db").exists()

    premarket = {"strikes": [{"call": _item(120, at.replace(hour=9, minute=14))}]}
    await store.enrich(premarket, now=at.replace(hour=9, minute=14, second=15))
    assert premarket["strikes"][0]["call"]["oi_change"] is None
    assert not (tmp_path / "safe.db").exists()


@pytest.mark.asyncio
async def test_new_trading_session_never_reuses_yesterdays_oi(tmp_path):
    store = KiteSessionOIBaselines(tmp_path / "sessions.db")
    friday = datetime(2026, 10, 9, 9, 30, tzinfo=IST)
    monday = datetime(2026, 10, 12, 9, 30, tzinfo=IST)
    for at, oi in [(friday, 5000), (monday, 8000)]:
        chain = {"strikes": [{"call": _item(oi, at)}]}
        await store.enrich(chain, now=at + timedelta(seconds=1))
        assert chain["strikes"][0]["call"]["oi_change"] is None
        assert chain["strikes"][0]["call"]["oi_baseline_at"] == at.isoformat()
