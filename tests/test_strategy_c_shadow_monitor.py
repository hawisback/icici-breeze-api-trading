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
        save_execution_ledger=AsyncMock(),
        save_decision_log=AsyncMock(),
    )


def _risk_config():
    return SimpleNamespace(
        paper_slippage_points=1.0,
        account_equity=500000.0,
        paper_brokerage_per_order=20.0,
        paper_exchange_charge_rate=0.0003503,
        paper_stt_sell_rate=0.001,
        paper_gst_rate=0.18,
        paper_sebi_charge_rate=0.000001,
        paper_stamp_buy_rate=0.00003,
        paper_cost_assumption_version="paper_options_costs_v1",
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


def _chain():
    return {
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


def _row():
    return {
        "candidate_signal_id": f"{CANDIDATE_ID}:2026-09-23T04:31:00Z:CALL",
        "direction": "CALL",
        "entry_time": "2026-09-23T04:31:00+00:00",
        "entry_price": 25000.0,
        "initial_stop": 24900.0,
        "lifecycle": {"status": "OPEN"},
    }


@pytest.mark.asyncio
async def test_shadow_monitor_no_active_futures_is_passive():
    repo = _repo()
    monitor = StrategyCShadowMonitor(repository=repo)
    await monitor.initialize()
    result = await monitor.observe(
        active_futures_instrument=None,
        now=datetime(2026, 9, 23, 4, 0, tzinfo=UTC),
    )
    assert result["status"] == "NO_ACTIVE_FUTURES_DATA"
    repo.save_option_chain_snapshot.assert_not_awaited()
    repo.save_option_quote.assert_not_awaited()
    repo.save_execution_ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_monitor_refuses_freeze_day_observation():
    repo = _repo()
    monitor = StrategyCShadowMonitor(repository=repo)
    await monitor.initialize()
    result = await monitor.observe(
        active_futures_instrument="INST-NIFTY-FUT-2026-09-29",
        now=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )
    assert result["status"] == "WAITING_FOR_POST_FREEZE_SESSION"
    assert result["freeze_date"] == "2026-09-22"
    repo.save_option_chain_snapshot.assert_not_awaited()
    repo.save_execution_ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_entry_opens_isolated_paper_position_and_never_has_oms():
    repo = _repo()
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
    risk_sizer.size.return_value = SimpleNamespace(
        option_loss_per_lot=1550.0,
        method="DELTA_APPROXIMATION",
        risk_budget=2500.0,
        lots=1,
        quantity=25,
        rejection_reason=None,
    )
    monitor = StrategyCShadowMonitor(
        repository=repo,
        option_chain_service=SimpleNamespace(get_chain=AsyncMock(return_value=_chain())),
        contract_selector=selector,
        risk_sizer=risk_sizer,
        risk_config=_risk_config(),
    )
    tracked = await monitor._capture_entry(
        _row(),
        observed_at=datetime(2026, 9, 23, 4, 31, 1, tzinfo=UTC),
    )
    snapshot = repo.save_option_chain_snapshot.await_args.args[0]
    assert snapshot["strategy"] == CANDIDATE_ID
    assert snapshot["execution_mode"] == "PAPER"
    assert snapshot["selected_contract"]["instrument_id"] == "OPT-C-1"
    assert tracked["paper_status"] == "OPEN"
    assert tracked["paper_lots"] == 1
    assert tracked["paper_quantity"] == 25
    assert tracked["entry_executable_price"] == 102.0
    assert tracked["capital_per_lot"] == 2550.0
    assert tracked["estimated_option_loss_per_lot"] == 1550.0
    ledger = repo.save_execution_ledger.await_args.args[0]
    assert ledger["side"] == "BUY"
    assert ledger["trade_id"].startswith("PAPER-C:")
    assert ledger["quantity"] == 25
    assert not hasattr(monitor, "oms")


@pytest.mark.asyncio
async def test_stale_signal_does_not_backfill_paper_entry():
    repo = _repo()
    selector = Mock()
    selector.select_contract.return_value = (_selected(), [], None)
    risk_sizer = Mock()
    risk_sizer.size.return_value = SimpleNamespace(
        option_loss_per_lot=1550.0,
        method="DELTA_APPROXIMATION",
        risk_budget=2500.0,
        lots=1,
        quantity=25,
        rejection_reason=None,
    )
    monitor = StrategyCShadowMonitor(
        repository=repo,
        option_chain_service=SimpleNamespace(get_chain=AsyncMock(return_value=_chain())),
        contract_selector=selector,
        risk_sizer=risk_sizer,
        risk_config=_risk_config(),
    )
    tracked = await monitor._capture_entry(
        _row(),
        observed_at=datetime(2026, 9, 23, 4, 40, 0, tzinfo=UTC),
    )
    assert tracked["paper_status"] == "SKIPPED"
    assert tracked["paper_rejection_reason"] == "STALE_SIGNAL_FOR_PAPER_ENTRY"
    repo.save_execution_ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_paper_close_uses_bid_side_slippage_and_costs_without_broker():
    repo = _repo()
    monitor = StrategyCShadowMonitor(repository=repo, risk_config=_risk_config())
    signal_id = _row()["candidate_signal_id"]
    tracked = {
        "signal_id": signal_id,
        "paper_status": "OPEN",
        "paper_quantity": 25,
        "paper_lots": 1,
        "entry_raw_ask": 101.0,
        "entry_executable_price": 102.0,
        "selected_contract": {"instrument_id": "OPT-C-1"},
    }
    lifecycle = {
        "status": "RESOLVED",
        "exit_time": "2026-09-23T05:00:00+00:00",
        "exit_reason": "HARD_TARGET",
        "realized_r": 2.0,
    }
    quote = {
        "status": "VALID",
        "quote_timestamp": "2026-09-23T05:00:01+00:00",
        "source": "BREEZE",
        "bid": 120.0,
        "ask": 121.0,
        "ltp": 120.5,
    }
    status = await monitor._attempt_paper_close(
        signal_id=signal_id,
        tracked=tracked,
        lifecycle=lifecycle,
        quote=quote,
        observed_at=datetime(2026, 9, 23, 5, 0, 2, tzinfo=UTC),
    )
    assert status == "CLOSED"
    assert tracked["paper_exit_executable_price"] == 119.0
    assert tracked["paper_gross_pnl"] == 425.0
    assert tracked["paper_transaction_costs"] > 0
    assert tracked["paper_net_pnl"] < tracked["paper_gross_pnl"]
    ledger = repo.save_execution_ledger.await_args.args[0]
    assert ledger["side"] == "SELL"
    assert ledger["executable_price"] == 119.0
    assert not hasattr(monitor, "oms")


@pytest.mark.asyncio
async def test_missing_timely_exit_quote_marks_paper_trade_incomplete():
    repo = _repo()
    monitor = StrategyCShadowMonitor(repository=repo, risk_config=_risk_config())
    tracked = {
        "paper_status": "OPEN",
        "paper_quantity": 25,
        "entry_executable_price": 102.0,
    }
    lifecycle = {
        "status": "RESOLVED",
        "exit_time": "2026-09-23T05:00:00+00:00",
        "exit_reason": "STOP_OR_TRAIL",
        "realized_r": -1.0,
    }
    status = await monitor._attempt_paper_close(
        signal_id="SIG-C",
        tracked=tracked,
        lifecycle=lifecycle,
        quote=None,
        observed_at=datetime(2026, 9, 23, 5, 3, 0, tzinfo=UTC),
    )
    assert status == "INCOMPLETE_EXIT_QUOTE"
    assert tracked["paper_rejection_reason"] == "NO_TIMELY_EXECUTABLE_EXIT_QUOTE"
    repo.save_execution_ledger.assert_not_awaited()


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
