from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.historical.strategy_c_candidate_manifest import CANDIDATE_ID, _spec_fingerprint
from services.strategy.models import OptionType, SelectedContract
from services.strategy.strategy_c_shadow_monitor import StrategyCShadowMonitor


UTC = timezone.utc


def _repo():
    return SimpleNamespace(
        get_runtime=AsyncMock(return_value={}),
        save_runtime=AsyncMock(),
        save_option_chain_snapshot=AsyncMock(),
        save_option_quote=AsyncMock(),
        save_decision_log=AsyncMock(),
    )


def _selected() -> SelectedContract:
    return SelectedContract(
        instrument_id="OPT-C-1",
        symbol="NIFTY 25000 CALL",
        expiry="2026-09-29",
        strike=25000,
        option_type=OptionType.CALL,
        ask_price=101,
        bid_price=100,
        open_interest=50000,
        volume=1000,
        spread_pct=1.0,
        lot_size=25,
        ltp=100.5,
        instrument_token="TOKEN-C-1",
        premium=101,
        delta=0.62,
        gamma=0.001,
        greek_source="BROKER",
        quote_timestamp=datetime(2026, 9, 23, 4, 31, tzinfo=UTC),
        quote_freshness_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_shadow_monitor_no_active_futures_is_passive():
    repo = _repo()
    monitor = StrategyCShadowMonitor(repository=repo)
    await monitor.initialize()
    result = await monitor.observe(active_futures_instrument=None)
    assert result["status"] == "NO_ACTIVE_FUTURES_DATA"
    repo.save_option_chain_snapshot.assert_not_awaited()
    repo.save_option_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_monitor_entry_capture_is_shadow_only_and_no_order_path():
    repo = _repo()
    chain = {
        "source": "BREEZE",
        "captured_at": "2026-09-23T04:31:00+00:00",
        "expiry": "2026-09-29",
        "strikes": [{
            "strike": 25000,
            "call": {
                "instrument_id": "OPT-C-1",
                "instrument_token": "TOKEN-C-1",
                "expiry": "2026-09-29",
                "bid": 100,
                "ask": 101,
                "ltp": 100.5,
                "delta": 0.62,
                "gamma": 0.001,
                "open_interest": 50000,
                "volume": 1000,
                "lot_size": 25,
                "quote_timestamp": "2026-09-23T04:31:00+00:00",
            },
        }],
    }
    selector = Mock()
    selector.select_contract.return_value = (
        _selected(),
        [{
            "strike": 25000,
            "status": "ELIGIBLE",
            "instrument_id": "OPT-C-1",
            "expiry": "2026-09-29",
            "bid": 100,
            "ask": 101,
            "delta": 0.62,
        }],
        None,
    )
    risk_sizer = Mock()
    risk_sizer.estimate_option_loss_per_lot.return_value = (1550.0, "DELTA_APPROXIMATION")
    monitor = StrategyCShadowMonitor(
        repository=repo,
        option_chain_service=SimpleNamespace(get_chain=AsyncMock(return_value=chain)),
        contract_selector=selector,
        risk_sizer=risk_sizer,
        risk_config=SimpleNamespace(paper_slippage_points=1.0),
    )
    row = {
        "candidate_signal_id": f"{CANDIDATE_ID}:2026-09-23T04:31:00Z:CALL",
        "direction": "CALL",
        "entry_time": "2026-09-23T04:31:00+00:00",
        "entry_price": 25000.0,
        "initial_stop": 24900.0,
        "lifecycle": {"status": "OPEN"},
    }
    tracked = await monitor._capture_entry(
        row,
        observed_at=datetime(2026, 9, 23, 4, 31, 1, tzinfo=UTC),
    )
    snapshot = repo.save_option_chain_snapshot.await_args.args[0]
    assert snapshot["strategy"] == CANDIDATE_ID
    assert snapshot["execution_mode"] == "SHADOW_ONLY"
    assert snapshot["selected_contract"]["instrument_id"] == "OPT-C-1"
    assert tracked["entry_executable_price"] == 102.0
    assert tracked["capital_per_lot"] == 2550.0
    assert tracked["estimated_option_loss_per_lot"] == 1550.0
    assert not hasattr(monitor, "oms")


@pytest.mark.asyncio
async def test_shadow_monitor_disables_on_fingerprint_drift():
    repo = _repo()
    repo.get_runtime.return_value = {
        "candidate_id": CANDIDATE_ID,
        "candidate_spec_fingerprint": "drifted",
    }
    monitor = StrategyCShadowMonitor(repository=repo)
    await monitor.initialize()
    assert monitor.disabled_reason == "FROZEN_CANDIDATE_FINGERPRINT_MISMATCH"
    result = await monitor.observe(active_futures_instrument="INST-NIFTY-FUT-2026-09-29")
    assert result["status"] == "DISABLED"
    assert _spec_fingerprint() != "drifted"
