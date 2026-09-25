from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncio

from libs.contracts.models import Candle
from services.strategy.models import (
    HistoricalReplaySource,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    StrategyName,
    StrategySignal,
    TradeDirection,
)
from services.strategy.replay_contract_selection import (
    HistoricalContractSelectionProvider,
    ReplayPriceEvidence,
)
from services.strategy.replay_execution_model import (
    estimate_fill,
    estimate_round_trip_execution,
)


UTC = timezone.utc


def _signal(
    *,
    signal_id: str = "SIG-A-EXACT",
    strategy: StrategyName = StrategyName.TREND_PULLBACK,
) -> StrategySignal:
    return StrategySignal(
        signal_id=signal_id,
        strategy=strategy,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=datetime(2026, 9, 17, 4, 30, tzinfo=UTC),
        spot_reference_price=24000.0,
        underlying_entry_price=24000.0 if strategy == StrategyName.TREND_PULLBACK else None,
        structural_stop=23950.0,
        r_points=50.0,
        derivatives_score=0.0,
    )


def _candidate(
    strike: float,
    delta: float,
    instrument_id: str,
) -> dict:
    ts = "2026-09-17T04:30:00+00:00"
    return {
        "strike": strike,
        "option_type": "CALL",
        "expiry": "2026-09-24",
        "bid": 99.0,
        "ask": 100.0,
        "mid": 99.5,
        "spread_points": 1.0,
        "spread_pct": 1.005,
        "delta": delta,
        "gamma": 0.01,
        "greek_source": "BROKER",
        "greek_timestamp": ts,
        "quote_timestamp": ts,
        "quote_freshness_seconds": 0.0,
        "open_interest": 50000,
        "volume": 1000,
        "lot_size": 50,
        "instrument_id": instrument_id,
        "symbol": instrument_id,
        "instrument_token": instrument_id,
        "ltp": 99.5,
        "status": "ELIGIBLE",
    }


def _snapshot(signal_id: str, candidates: list[dict]) -> dict:
    return {
        "snapshot_id": "SNAP-1",
        "strategy_signal_id": signal_id,
        "captured_at": "2026-09-17T04:30:01+00:00",
        "selector_timestamp": "2026-09-17T04:30:00+00:00",
        "signal_timestamp": "2026-09-17T04:30:00+00:00",
        "chain_snapshot_timestamp": "2026-09-17T04:30:00+00:00",
        "spot_price": 24000.0,
        "expiry": "2026-09-24",
        "source": "KITE",
        "strategy": StrategyName.TREND_PULLBACK.value,
        "direction": TradeDirection.BULLISH.value,
        "selector_candidates": candidates,
        "chain_candidates": [],
        "selected_contract": None,
        "selector_result": "SELECTED",
        "rejection_reason": None,
    }


def test_exact_signal_snapshot_reruns_production_contract_selector():
    signal = _signal()
    provider = HistoricalContractSelectionProvider(
        historical_service=None,
        strategy_repository=None,
        option_config=OptionSelectionConfig(),
    )
    provider.date_str = "2026-09-17"
    provider.snapshots = [
        _snapshot(
            signal.signal_id,
            [
                _candidate(23950.0, 0.58, "OPT-23950"),
                _candidate(24000.0, 0.62, "OPT-24000"),
            ],
        )
    ]

    decision = asyncio.run(provider.select_contract(signal))

    assert decision.evidence_status == "POINT_IN_TIME_CHAIN_SNAPSHOT"
    assert decision.actual_method == "PRODUCTION_CONTRACT_SELECTOR"
    assert decision.production_rules_applied is True
    assert decision.selected_contract is not None
    assert decision.selected_contract.instrument_id == "OPT-24000"
    assert decision.selected_contract.delta == 0.62
    assert decision.entry_price_basis == "POINT_IN_TIME_ASK"
    assert decision.entry_reference_price == 100.0
    assert decision.unsupported_evidence == ()


def test_exact_snapshot_rejection_never_falls_back_to_approximation():
    signal = _signal()
    provider = HistoricalContractSelectionProvider(
        historical_service=None,
        strategy_repository=None,
        option_config=OptionSelectionConfig(),
    )
    provider.date_str = "2026-09-17"
    provider.snapshots = [
        _snapshot(
            signal.signal_id,
            [_candidate(24000.0, 0.20, "OPT-BAD-DELTA")],
        )
    ]
    provider.option_universe = [
        SimpleNamespace(
            instrument_id="OPT-APPROX",
            strike=24000.0,
            expiry="2026-09-24",
            option_right=SimpleNamespace(value="CALL"),
            lot_size=50,
            segment="OPTIONS",
        )
    ]

    decision = asyncio.run(provider.select_contract(signal))

    assert decision.evidence_status == "POINT_IN_TIME_CHAIN_SNAPSHOT"
    assert decision.actual_method == "PRODUCTION_CONTRACT_SELECTOR"
    assert decision.production_rules_applied is True
    assert decision.selected_contract is None
    assert decision.rejection_reason == "NO_ELIGIBLE_DELTA_AWARE_CONTRACT"


def test_missing_chain_uses_explicit_approximated_selection():
    signal = _signal(signal_id="SIG-A-APPROX")
    contract = SimpleNamespace(
        instrument_id="OPT-APPROX",
        stock_code="NIFTY24000CE",
        strike=24000.0,
        expiry="2026-09-24",
        option_right=SimpleNamespace(value="CALL"),
        lot_size=50,
        segment="OPTIONS",
    )
    provider = HistoricalContractSelectionProvider(
        historical_service=None,
        strategy_repository=None,
        option_config=OptionSelectionConfig(),
    )
    provider.date_str = "2026-09-17"
    provider.historical_source = HistoricalReplaySource.BREEZE
    provider.option_universe = [contract]
    provider.candle_cache[contract.instrument_id] = [
        Candle(
            instrument_id=contract.instrument_id,
            interval="1m",
            start_time=signal.timestamp - timedelta(minutes=1),
            end_time=signal.timestamp,
            open=120.0,
            high=121.0,
            low=119.0,
            close=120.0,
            volume=100,
            source="BREEZE",
        )
    ]

    decision = asyncio.run(provider.select_contract(signal))

    assert decision.evidence_status == "APPROXIMATED_SELECTION"
    assert decision.actual_method == "APPROXIMATED_SELECTION"
    assert decision.production_rules_applied is False
    assert decision.selected_contract.instrument_id == contract.instrument_id
    assert decision.entry_price_basis == "HISTORICAL_COMPLETED_CANDLE_CLOSE_MARK"
    assert decision.entry_reference_price == 120.0
    assert "DELTA" in decision.unsupported_evidence
    assert "BID_ASK" in decision.unsupported_evidence


def test_future_quote_is_never_used_for_exit_fill_evidence():
    signal = _signal(signal_id="SIG-QUOTE")
    event = signal.timestamp + timedelta(minutes=10)
    instrument_id = "OPT-QUOTE"
    provider = HistoricalContractSelectionProvider(
        historical_service=None,
        strategy_repository=None,
        option_config=OptionSelectionConfig(),
    )
    provider.date_str = "2026-09-17"
    provider.historical_source = HistoricalReplaySource.BREEZE
    provider.quotes = [
        {
            "strategy_signal_id": signal.signal_id,
            "instrument_id": instrument_id,
            "quote_timestamp": (event + timedelta(seconds=1)).isoformat(),
            "source": "KITE",
            "bid": 110.0,
            "ask": 111.0,
            "ltp": 110.5,
            "status": "VALID",
        }
    ]
    provider.candle_cache[instrument_id] = [
        Candle(
            instrument_id=instrument_id,
            interval="1m",
            start_time=event - timedelta(minutes=1),
            end_time=event,
            open=105.0,
            high=106.0,
            low=104.0,
            close=105.0,
            volume=100,
            source="BREEZE",
        )
    ]

    evidence = asyncio.run(
        provider.price_evidence(
            instrument_id=instrument_id,
            signal_id=signal.signal_id,
            event_time=event,
            side="SELL",
        )
    )

    assert evidence.basis == "HISTORICAL_COMPLETED_CANDLE_CLOSE_MARK"
    assert evidence.mark_price == 105.0
    assert evidence.bid_ask_available is False


def test_bid_ask_fill_and_mark_fallback_are_explicitly_different():
    risk = RiskConfig(paper_slippage_points=1.0)
    event = datetime(2026, 9, 17, 5, 0, tzinfo=UTC)
    entry = ReplayPriceEvidence(
        status="AVAILABLE",
        basis="POINT_IN_TIME_BID_ASK",
        source="KITE",
        event_timestamp=event,
        evidence_timestamp=event,
        bid=99.0,
        ask=100.0,
    )
    exit_ = ReplayPriceEvidence(
        status="AVAILABLE",
        basis="POINT_IN_TIME_BID_ASK",
        source="KITE",
        event_timestamp=event + timedelta(minutes=30),
        evidence_timestamp=event + timedelta(minutes=30),
        bid=110.0,
        ask=111.0,
    )

    execution = estimate_round_trip_execution(
        entry_evidence=entry,
        exit_evidence=exit_,
        quantity=50,
        risk_config=risk,
    )

    assert execution.entry.executable_price == 101.0
    assert execution.exit.executable_price == 109.0
    assert execution.entry.executable_quote_equivalent is True
    assert execution.exit.executable_quote_equivalent is True
    assert execution.gross_execution_pnl == 400.0
    assert execution.slippage_cost == 100.0
    assert execution.transaction_costs is not None
    assert execution.net_execution_pnl < execution.gross_execution_pnl

    mark = ReplayPriceEvidence(
        status="AVAILABLE",
        basis="HISTORICAL_COMPLETED_CANDLE_CLOSE_MARK",
        source="BREEZE",
        event_timestamp=event,
        evidence_timestamp=event,
        mark_price=100.0,
    )
    fallback = estimate_fill(mark, side="BUY", risk_config=risk)
    assert fallback.executable_price == 101.0
    assert fallback.executable_quote_equivalent is False
    assert fallback.method == "COMPLETED_MARK_PLUS_CONFIGURED_SLIPPAGE_ESTIMATE"
    assert "not an executable quote" in str(fallback.reason)
