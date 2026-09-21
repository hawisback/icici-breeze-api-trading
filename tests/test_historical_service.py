"""Unit tests for HistoricalService with ICICI Breeze historical candle integration."""

from datetime import date, datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from libs.contracts.models import Candle
from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService
from services.instrument.repository import InstrumentRepository
from services.instrument.service import InstrumentService
from services.market_data.service import MarketDataService


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
async def test_targeted_breeze_window_sends_option_contract_fields(tmp_path):
    """Breeze V2 requires the underlying plus explicit option identity."""
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()

    mock_sdk = MagicMock()
    mock_sdk.get_historical_data_v2.return_value = {
        "Status": 200,
        "Success": [{
            "datetime": "2026-09-17 09:20:00",
            "open": "120.0", "high": "121.0", "low": "119.0", "close": "120.5",
            "volume": "100", "open_interest": "1000",
        }],
    }
    mock_client_mgr = MagicMock()
    mock_client_mgr.is_active = True
    mock_client_mgr.get_sdk_client.return_value = mock_sdk
    mock_client_mgr.sdk_runner = MagicMock()
    mock_client_mgr.sdk_runner.run = AsyncMock(side_effect=lambda fn, timeout_sec=None: fn())
    mock_gateway = MagicMock()
    mock_gateway.breeze_adapter.client_manager = mock_client_mgr
    mock_gateway.breeze_adapter.rate_limiter = None
    instrument_service = SimpleNamespace(
        get_instrument=AsyncMock(return_value=SimpleNamespace(
            segment="OPTIONS", underlying="NIFTY", exchange="NFO", expiry="2026-09-22",
            strike=23300.0, option_right=SimpleNamespace(value="PUT"),
        ))
    )
    service = HistoricalService(repository=repo, broker_gateway=mock_gateway, instrument_service=instrument_service)

    await service.fetch_candles_from_breeze_window(
        "INST-NIFTY-2026-09-22-23300-PE",
        interval="1m",
        start_time=datetime(2026, 9, 17, 3, 50, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 17, 3, 51, tzinfo=timezone.utc),
    )

    kwargs = mock_sdk.get_historical_data_v2.call_args.kwargs
    assert kwargs["stock_code"] == "NIFTY"
    assert kwargs["exchange_code"] == "NFO"
    assert kwargs["product_type"] == "options"
    assert kwargs["expiry_date"] == "2026-09-22T06:00:00.000Z"
    assert kwargs["right"] == "put"
    assert kwargs["strike_price"] == "23300.0"


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


@pytest.mark.asyncio
async def test_instrument_service_ensures_current_nifty_futures_even_when_seed_exists(tmp_path):
    repo = InstrumentRepository(db_path=tmp_path / "instruments.db")
    service = InstrumentService(repository=repo)
    await service.initialize()
    futures = await repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
    futures = [item for item in futures if item.segment == "FUTURES"]
    expiries = {item.expiry for item in futures}
    assert "2026-09-29" in expiries or date.today().year != 2026
    assert len(futures) >= 2
    assert all(item.instrument_id.startswith("INST-NIFTY-FUT-") for item in futures)


@pytest.mark.asyncio
async def test_breeze_futures_contract_args_are_nfo_futures_with_expiry(tmp_path):
    instrument_service = SimpleNamespace(
        get_instrument=AsyncMock(return_value=SimpleNamespace(
            segment="FUTURES", underlying="NIFTY", exchange="NFO",
            expiry="2026-09-29",
        ))
    )
    service = HistoricalService(instrument_service=instrument_service)
    args = await service._breeze_contract_args("INST-NIFTY-FUT-2026-09-29")
    assert args == {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "product_type": "futures",
        "expiry_date": "2026-09-29T07:00:00.000Z",
        "right": "others",
        "strike_price": "0",
    }


def test_live_breeze_session_is_detected_via_client_manager():
    gateway = SimpleNamespace(
        active_adapter=SimpleNamespace(),
        breeze_adapter=SimpleNamespace(client_manager=SimpleNamespace(is_active=True)),
    )
    service = MarketDataService(broker_gateway=gateway)
    assert service._live_broker_active() is True


@pytest.mark.asyncio
async def test_get_candles_recognizes_authenticated_breeze_client_manager(tmp_path):
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()
    gateway = SimpleNamespace(
        active_adapter=SimpleNamespace(is_active=False),
        breeze_adapter=SimpleNamespace(client_manager=SimpleNamespace(is_active=True)),
    )
    service = HistoricalService(repository=repo, broker_gateway=gateway)
    service.fetch_candles_from_breeze = AsyncMock(return_value=[
        Candle(
            instrument_id="INST-NIFTY-INDEX", interval="5m",
            start_time=datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 21, 4, 5, tzinfo=timezone.utc),
            open=23400, high=23420, low=23390, close=23410, volume=100, source="BREEZE",
        )
    ])
    candles = await service.get_candles("INST-NIFTY-INDEX", "5m", allow_synthetic_fallback=False)
    assert candles and candles[-1].close == 23410
    assert service.fetch_candles_from_breeze.await_count >= 1


@pytest.mark.asyncio
async def test_runtime_history_does_not_cross_from_kite_to_breeze(tmp_path):
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29", interval="15m",
        start_time=datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 21, 4, 15, tzinfo=timezone.utc),
        open=23400, high=23420, low=23390, close=23410, volume=100, source="KITE",
    )
    kite = SimpleNamespace(is_active=True, fetch_historical_candles=AsyncMock(return_value=[candle]))
    gateway = SimpleNamespace(
        active_broker_name="kite", active_adapter=kite,
        breeze_adapter=SimpleNamespace(client_manager=SimpleNamespace(is_active=True)),
    )
    service = HistoricalService(repository=repo, broker_gateway=gateway)
    service.fetch_candles_from_breeze = AsyncMock(return_value=[])
    candles = await service.get_candles(
        "INST-NIFTY-FUT-2026-09-29", "15m",
        requested_source="MIXED", allow_synthetic_fallback=False,
    )
    assert candles and all(item.source == "KITE" for item in candles)
    kite.fetch_historical_candles.assert_awaited_once()
    service.fetch_candles_from_breeze.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_history_does_not_cross_from_breeze_to_kite(tmp_path):
    repo = HistoricalRepository(db_path=tmp_path / "historical.db")
    await repo.initialize()
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29", interval="15m",
        start_time=datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 21, 4, 15, tzinfo=timezone.utc),
        open=23400, high=23420, low=23390, close=23410, volume=100, source="BREEZE",
    )
    kite = SimpleNamespace(is_active=True, fetch_historical_candles=AsyncMock(return_value=[]))
    gateway = SimpleNamespace(
        active_broker_name="breeze", active_adapter=SimpleNamespace(),
        breeze_adapter=SimpleNamespace(client_manager=SimpleNamespace(is_active=True)),
        kite_adapter=kite,
    )
    service = HistoricalService(repository=repo, broker_gateway=gateway)
    service.fetch_candles_from_breeze = AsyncMock(return_value=[candle])
    candles = await service.get_candles(
        "INST-NIFTY-FUT-2026-09-29", "15m",
        requested_source="MIXED", allow_synthetic_fallback=False,
    )
    assert candles and all(item.source == "BREEZE" for item in candles)
    service.fetch_candles_from_breeze.assert_awaited_once()
    kite.fetch_historical_candles.assert_not_awaited()
