"""Unit tests for HistoricalService with ICICI Breeze historical candle integration."""

from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from libs.contracts.models import Candle
from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService


@pytest.mark.asyncio
async def test_historical_service_mapping():
    """Verify instrument and interval mapping for ICICI Breeze API."""
    service = HistoricalService()

    # Instrument mapping
    assert service._map_instrument_to_breeze("INST-NIFTY-INDEX") == ("NIFTY", "NSE", "cash")
    assert service._map_instrument_to_breeze("NIFTY 50") == ("NIFTY", "NSE", "cash")
    assert service._map_instrument_to_breeze("INST-BANKNIFTY-INDEX") == ("CNXBAN", "NSE", "cash")
    assert service._map_instrument_to_breeze("NIFTY BANK") == ("CNXBAN", "NSE", "cash")
    assert service._map_instrument_to_breeze("RELIANCE") == ("RELIND", "NSE", "cash")

    # Interval mapping
    assert service._map_interval_to_breeze("1m") == ("1minute", 1)
    assert service._map_interval_to_breeze("5m") == ("5minute", 5)
    assert service._map_interval_to_breeze("30m") == ("30minute", 30)
    assert service._map_interval_to_breeze("1D") == ("1day", 1440)


@pytest.mark.asyncio
async def test_historical_service_breeze_integration(tmp_path):
    """Verify HistoricalService fetches real candles from Breeze adapter and caches in DB."""
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()

    # Mock Breeze Gateway and Client Manager
    mock_sdk = MagicMock()
    mock_sdk.get_historical_data_v2.return_value = {
        "Status": 200,
        "Success": [
            {
                "datetime": "2026-09-16 15:25:00",
                "open": "23214.10",
                "high": "23224.15",
                "low": "23207.50",
                "close": "23217.60",
                "volume": "1420",
                "open_interest": "45000",
            },
            {
                "datetime": "2026-09-16 15:30:00",
                "open": "23217.60",
                "high": "23217.60",
                "low": "23217.60",
                "close": "23217.60",
                "volume": "0",
                "open_interest": "45000",
            },
        ],
    }

    mock_client_mgr = MagicMock()
    mock_client_mgr.is_active = True
    mock_client_mgr.get_sdk_client.return_value = mock_sdk
    mock_client_mgr.sdk_runner = MagicMock()
    mock_client_mgr.sdk_runner.run = AsyncMock(side_effect=lambda fn, timeout_sec=None: fn())

    mock_breeze_adapter = MagicMock()
    mock_breeze_adapter.client_manager = mock_client_mgr
    mock_breeze_adapter.rate_limiter = None

    mock_gateway = MagicMock()
    mock_gateway.breeze_adapter = mock_breeze_adapter

    service = HistoricalService(repository=repo, broker_gateway=mock_gateway)

    # First fetch: should call Breeze and save to repo
    candles = await service.get_candles("INST-NIFTY-INDEX", interval="5m", limit=10)
    assert len(candles) == 2
    assert candles[-1].close == 23217.60
    assert candles[-1].source == "BREEZE"

    # Second fetch: should retrieve from SQLite repo
    cached = await repo.get_candles("INST-NIFTY-INDEX", interval="5m")
    assert len(cached) == 2
    assert cached[-1].close == 23217.60
    assert cached[-1].source == "BREEZE"


@pytest.mark.asyncio
async def test_targeted_breeze_window_persists_one_minute_candles(tmp_path):
    """Replay support fetches only the requested 1m window and normalizes UTC."""
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()

    mock_sdk = MagicMock()
    mock_sdk.get_historical_data_v2.return_value = {
        "Status": 200,
        "Success": [
            {
                "datetime": "2026-09-16 15:24:00",
                "open": "23210.00",
                "high": "23212.00",
                "low": "23209.00",
                "close": "23211.00",
                "volume": "12",
            },
            {
                "datetime": "2026-09-16 15:25:00",
                "open": "23211.00",
                "high": "23214.00",
                "low": "23210.00",
                "close": "23213.00",
                "volume": "15",
            },
        ],
    }
    mock_client_mgr = MagicMock()
    mock_client_mgr.is_active = True
    mock_client_mgr.get_sdk_client.return_value = mock_sdk
    mock_client_mgr.sdk_runner = MagicMock()
    mock_client_mgr.sdk_runner.run = AsyncMock(side_effect=lambda fn, timeout_sec=None: fn())
    mock_breeze_adapter = MagicMock()
    mock_breeze_adapter.client_manager = mock_client_mgr
    mock_breeze_adapter.rate_limiter = None
    mock_gateway = MagicMock()
    mock_gateway.breeze_adapter = mock_breeze_adapter

    service = HistoricalService(repository=repo, broker_gateway=mock_gateway)
    candles = await service.fetch_candles_from_breeze_window(
        "INST-NIFTY-INDEX",
        interval="1m",
        start_time=datetime(2026, 9, 16, 9, 54, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 16, 9, 55, tzinfo=timezone.utc),
    )

    assert len(candles) == 2
    assert candles[0].start_time == datetime(2026, 9, 16, 9, 54, tzinfo=timezone.utc)
    assert candles[0].interval == "1m"
    assert candles[0].source == "BREEZE"
    cached = await repo.get_candles("INST-NIFTY-INDEX", "1m")
    assert len(cached) == 2


@pytest.mark.asyncio
async def test_historical_service_offline_fallback(tmp_path):
    """Verify HistoricalService falls back to synthetic data when Breeze is offline."""
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()

    # No broker gateway connected
    service = HistoricalService(repository=repo, broker_gateway=None)

    candles = await service.get_candles("INST-NIFTY-INDEX", interval="5m", limit=10)
    assert len(candles) == 10
    assert candles[0].source == "SIMULATED"
