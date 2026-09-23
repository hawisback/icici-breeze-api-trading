from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.historical.strategy_d_candidate_manifest import (
    CANDIDATE_ID,
    spec_fingerprint,
)
from services.strategy.models import RiskConfig, SessionTimersConfig
from services.strategy.strategy_d_paper_monitor import StrategyDPaperMonitor


UTC = timezone.utc


def _repo():
    return SimpleNamespace(
        get_runtime=AsyncMock(return_value={}),
        save_runtime=AsyncMock(),
        save_decision_log=AsyncMock(),
        save_execution_ledger=AsyncMock(),
    )


def _monitor():
    return StrategyDPaperMonitor(
        repository=_repo(),
        risk_config=RiskConfig(
            paper_slippage_points=1.0,
            account_equity=500000.0,
        ),
        session_config=SessionTimersConfig(),
    )


@pytest.mark.asyncio
async def test_d_monitor_is_frozen_paper_only_and_has_no_oms():
    monitor = _monitor()
    await monitor.initialize()

    assert monitor.config.strategy_id == CANDIDATE_ID
    assert monitor.runtime["candidate_spec_fingerprint"] == spec_fingerprint()
    assert monitor.config.variant == "V2_CANDIDATE"
    assert not hasattr(monitor, "oms")


@pytest.mark.asyncio
async def test_t1_pending_quote_is_retried_and_filled():
    monitor = _monitor()
    tracked = {
        "signal_id": "SIG-D",
        "paper_partial_status": "T1_QUOTE_PENDING",
        "paper_lots": 2,
        "paper_initial_quantity": 130,
        "paper_remaining_quantity": 130,
        "entry_executable_price": 100.0,
        "selected_contract": {"lot_size": 65},
        "sell_legs": [],
    }
    lifecycle = SimpleNamespace(
        scale_out_time=datetime(2026, 9, 23, 5, 0, tzinfo=UTC),
    )
    quote = {
        "status": "VALID",
        "quote_timestamp": "2026-09-23T05:00:01+00:00",
        "source": "BREEZE",
        "bid": 120.0,
        "ask": 121.0,
        "ltp": 120.5,
    }

    await monitor._apply_partial_if_due(
        tracked,
        lifecycle=lifecycle,
        quote=quote,
        observed_at=datetime(2026, 9, 23, 5, 0, 2, tzinfo=UTC),
    )

    assert tracked["paper_partial_status"] == "FILLED"
    assert tracked["paper_partial_quantity"] == 65
    assert tracked["paper_remaining_quantity"] == 65
    assert tracked["paper_partial_fill"] == 119.0
    ledger = monitor.repo.save_execution_ledger.await_args.args[0]
    assert ledger["side"] == "SELL"
    assert ledger["quantity"] == 65
    assert ledger["reason"] == "STRATEGY_D_T1_PARTIAL"


@pytest.mark.asyncio
async def test_missing_required_t1_fill_marks_trade_incomplete_not_profitable():
    monitor = _monitor()
    tracked = {
        "signal_id": "SIG-D",
        "paper_status": "OPEN",
        "paper_partial_status": "INCOMPLETE_T1_QUOTE",
        "paper_lots": 2,
        "paper_initial_quantity": 130,
        "paper_remaining_quantity": 130,
        "entry_executable_price": 100.0,
        "sell_legs": [],
    }
    lifecycle = SimpleNamespace(
        exit_time=datetime(2026, 9, 23, 5, 10, tzinfo=UTC),
        runner_exit_reason="EMA9_CLOSE_CROSS",
        realized_r=1.0,
    )
    quote = {
        "status": "VALID",
        "quote_timestamp": "2026-09-23T05:10:01+00:00",
        "source": "BREEZE",
        "bid": 125.0,
        "ask": 126.0,
        "ltp": 125.5,
    }

    await monitor._close_if_resolved(
        tracked,
        lifecycle=lifecycle,
        quote=quote,
        observed_at=datetime(2026, 9, 23, 5, 10, 2, tzinfo=UTC),
    )

    assert tracked["paper_status"] == "INCOMPLETE_T1_QUOTE"
    assert tracked["paper_rejection_reason"] == "T1_PARTIAL_FILL_UNAVAILABLE"
    assert "paper_net_pnl" not in tracked
    monitor.repo.save_execution_ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_lot_does_not_require_impossible_half_lot_exit():
    monitor = _monitor()
    tracked = {
        "signal_id": "SIG-D",
        "paper_partial_status": "NOT_REACHED",
        "paper_lots": 1,
        "paper_initial_quantity": 65,
        "paper_remaining_quantity": 65,
        "entry_executable_price": 100.0,
        "selected_contract": {"lot_size": 65},
        "sell_legs": [],
    }
    lifecycle = SimpleNamespace(
        scale_out_time=datetime(2026, 9, 23, 5, 0, tzinfo=UTC),
    )

    await monitor._apply_partial_if_due(
        tracked,
        lifecycle=lifecycle,
        quote={"status": "UNAVAILABLE"},
        observed_at=datetime(2026, 9, 23, 5, 0, 2, tzinfo=UTC),
    )

    assert tracked["paper_partial_status"] == "ONE_LOT_NO_PARTIAL"
    assert tracked["paper_remaining_quantity"] == 65
    monitor.repo.save_execution_ledger.assert_not_awaited()
