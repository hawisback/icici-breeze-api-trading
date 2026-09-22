from datetime import date, datetime, time, timezone

import pytest

from services.historical.breeze_backfill import IST
from services.historical.breeze_futures_1m_backfill import (
    BreezeFutures1mBackfill,
    Futures1mBackfillConfig,
)


def _backfill(tmp_path):
    return BreezeFutures1mBackfill(
        Futures1mBackfillConfig(
            start_date=date(2026, 9, 22),
            end_date=date(2026, 9, 22),
            historical_db_path=tmp_path / "historical.db",
            instruments_db_path=tmp_path / "instruments.db",
        ),
        settings=object(),
    )


def test_futures_1m_backfill_normalizes_native_breeze_rows(tmp_path):
    backfill = _backfill(tmp_path)
    start = datetime.combine(date(2026, 9, 22), time(9, 15), tzinfo=IST)
    end = datetime.combine(date(2026, 9, 22), time(15, 30), tzinfo=IST)

    candles = backfill._normalize_rows(
        [
            {
                "datetime": "2026-09-22 09:15:00",
                "open": "25000",
                "high": "25010",
                "low": "24995",
                "close": "25005",
                "volume": "100",
                "open_interest": "200",
            }
        ],
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        window_start=start.astimezone(timezone.utc),
        window_end=end.astimezone(timezone.utc),
    )

    assert len(candles) == 1
    candle = candles[0]
    assert candle.interval == "1m"
    assert candle.instrument_id == "INST-NIFTY-FUT-2026-09-29"
    assert candle.start_time.astimezone(IST).strftime("%H:%M") == "09:15"
    assert candle.end_time.astimezone(IST).strftime("%H:%M") == "09:16"
    assert candle.source == "BREEZE"


@pytest.mark.asyncio
async def test_futures_1m_backfill_is_additive(tmp_path):
    backfill = _backfill(tmp_path)
    await backfill.repo.initialize()

    start = datetime.combine(date(2026, 9, 22), time(9, 15), tzinfo=IST)
    end = datetime.combine(date(2026, 9, 22), time(15, 30), tzinfo=IST)
    candles = backfill._normalize_rows(
        [
            {
                "datetime": "2026-09-22 09:15:00",
                "open": "25000",
                "high": "25010",
                "low": "24995",
                "close": "25005",
                "volume": "100",
                "open_interest": "200",
            }
        ],
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        window_start=start.astimezone(timezone.utc),
        window_end=end.astimezone(timezone.utc),
    )

    await backfill._save_additive(candles)
    await backfill._save_additive(candles)

    stored = await backfill.repo.get_candles(
        "INST-NIFTY-FUT-2026-09-29",
        "1m",
        limit=10,
    )
    assert len(stored) == 1
    assert backfill.report.candles_inserted == 1
    assert backfill.report.candles_skipped_existing == 1
