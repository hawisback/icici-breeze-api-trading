from datetime import UTC, datetime

import pytest

from services.strategy.models import OptionType, StrategyName, StrategySignal, TradeDirection
from services.strategy.replay_manifest import (
    ReplayEvent,
    ReplayManifestRecorder,
    ReplayStateSnapshot,
)


def signal() -> StrategySignal:
    return StrategySignal(
        signal_id="SIG-A-BULLISH-1",
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=datetime(2026, 9, 18, 9, 30, tzinfo=UTC),
        spot_reference_price=100.0,
        structural_stop=95.0,
        r_points=5.0,
        derivatives_score=0.0,
        features_snapshot={
            "pullback_low": 97.0,
            "pullback_high": 101.0,
            "impulse_low": 96.0,
            "impulse_high": 104.0,
            "atr": 3.0,
        },
    )


def record_entry(recorder: ReplayManifestRecorder) -> None:
    recorder.record_entry(
        signal=signal(),
        trading_date="2026-09-18",
        trigger_source_candle_timestamp="2026-09-18T09:25:00+00:00",
        trigger_level=100.0,
        simulated_entry_timestamp="2026-09-18T09:30:00+00:00",
        simulated_entry_price=100.0,
        entry_5m_candle_timestamp="2026-09-18T09:25:00+00:00",
        entry_occurred_intrabar=True,
        setup_id="CALL:impulse->pullback",
        pullback_swing_low=97.0,
        pullback_swing_high=101.0,
        impulse_low=96.0,
        impulse_high=104.0,
        atr_at_entry=3.0,
        initial_structural_stop=95.0,
        initial_risk_points=5.0,
        initial_risk_atr=1.6667,
        current_trailing_stop=95.0,
        current_r=0.0,
        highest_favorable_price=100.0,
        lowest_favorable_price=100.0,
        peak_r=0.0,
        protected_breakeven_active=False,
        profit_lock_active=False,
        runner_mode_active=False,
        current_ladder_stage="OPEN_INITIAL_RISK",
        reversal_score=0,
        adverse_health_counters={},
        entry_bar_timestamp="2026-09-18T09:25:00+00:00",
        last_managed_completed_bar_timestamp=None,
    )


def test_manifest_preserves_explicit_entry_state_and_timeline():
    recorder = ReplayManifestRecorder()
    record_entry(recorder)
    before = ReplayStateSnapshot(
        timestamp=datetime(2026, 9, 18, 9, 35, tzinfo=UTC),
        active_stop=95.0,
        ladder_stage="OPEN_INITIAL_RISK",
        current_r=0.4,
        peak_r=0.4,
        protected_breakeven_active=False,
        profit_lock_active=False,
        runner_mode_active=False,
    )
    after = before.model_copy(update={"active_stop": 98.0, "peak_r": 1.0})
    recorder.record_state_timeline(
        "SIG-A-BULLISH-1",
        before=before,
        after=after,
        exit_event=ReplayEvent(
            event="+1R_REACHED",
            timestamp=after.timestamp,
            reference_price=105.0,
            active_stop=98.0,
            r_multiple=1.0,
            source_candle=after.timestamp,
        ),
    )
    result = recorder.records()[0]
    assert result.current_trailing_stop == 95.0
    assert result.state_timeline[0]["before"].active_stop == 95.0
    assert result.state_timeline[0]["after"].active_stop == 98.0
    assert result.events[0].event == "ENTRY"
    assert result.events[-1].event == "+1R_REACHED"
    assert recorder.validate_complete(expected_count=1) == {
        "records": 1,
        "missing_mandatory_fields": 0,
        "expected_count_gap": 0,
    }


def test_duplicate_signal_ids_are_rejected():
    recorder = ReplayManifestRecorder()
    record_entry(recorder)
    with pytest.raises(ValueError, match="duplicate replay manifest signal"):
        record_entry(recorder)
