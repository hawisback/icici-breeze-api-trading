from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.historical.strategy_a_entry_timing import (
    build_entry_timing_report,
    enrich_rows,
)
from services.historical.strategy_a_v3_momentum_validation import (
    _momentum_context,
    _variant_pass,
)
from services.historical.strategy_a_v3_candidate_research import (
    build_report as build_v3_candidate_report,
    candidate_catalog,
)
from services.historical.strategy_a_research import (
    ResearchVariant,
    _row_passes,
    _selected_rows,
    _trigger_label,
    _walk_forward_folds,
)
from services.strategy.futures_signal import FuturesFeatureSnapshot
from services.strategy.models import StrategyTunablesConfig


UTC = timezone.utc


def _research_row() -> dict:
    return {
        "date": "2026-09-21",
        "timestamp": "2026-09-21T06:30:00+00:00",
        "direction": "CALL",
        "ema_order_directional": True,
        "di_directional": True,
        "adx14": 22.0,
        "ema_separation_atr": 0.10,
        "confirmation_directional": True,
        "confirmation_body_ratio": 0.40,
        "confirmation_close_location_pct": 0.30,
        "confirmation_range_atr": 1.50,
        "sr_present": True,
        "sr_touch_distance_atr": 0.10,
        "ema_distance_atr": 0.25,
        "vwap_distance_atr": 0.50,
        "risk_atr": 0.80,
        "opposing_room_r": 1.50,
        "label": {
            "trigger_status": "TRIGGERED",
            "t1_first_hit_r": 1.5,
            "max_favorable_r": 1.8,
            "max_adverse_r": 0.4,
            "hit_t1_before_stop": True,
            "hit_runner_before_stop": False,
        },
    }


def _bar(start: datetime, *, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="15m",
        start_time=start,
        end_time=start + timedelta(minutes=15),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def _feature(bar: Candle, atr: float = 2.0) -> FuturesFeatureSnapshot:
    return FuturesFeatureSnapshot(
        contract_id=bar.instrument_id,
        candle_timestamp=bar.end_time,
        candle_start=bar.start_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        ema20=100.0,
        ema50=99.0,
        adx14=25.0,
        plus_di14=30.0,
        minus_di14=10.0,
        atr14=atr,
        session_vwap=100.0,
        support=99.0,
        resistance=110.0,
        bar_index=100,
    )


def test_research_baseline_matches_boundary_thresholds_and_ablation_is_one_rule_only():
    config = StrategyTunablesConfig()
    row = _research_row()

    assert _row_passes(row, config=config) is True

    row["confirmation_body_ratio"] = 0.39
    assert _row_passes(row, config=config) is False

    variant = ResearchVariant(
        name="without_confirmation_body",
        parameter_overrides={},
        ignored_rules=frozenset({"confirmation_body"}),
    )
    assert _selected_rows([row], variant, config) == [row]


def test_research_label_uses_conservative_stop_first_for_same_bar_ambiguity():
    config = StrategyTunablesConfig()
    start = datetime(2026, 9, 21, 4, 15, tzinfo=UTC)
    trigger_bar = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    ambiguous = _bar(
        start + timedelta(minutes=15),
        open_=101.5,
        high=104.5,
        low=98.5,
        close=103.0,
    )
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[trigger_bar, ambiguous],
        future_features={trigger_bar.end_time: _feature(trigger_bar)},
        config=config,
    )

    assert label["trigger_status"] == "TRIGGERED"
    assert label["hit_t1_before_stop"] is False
    assert label["t1_first_hit_r"] == -1.0


def test_research_label_tracks_runner_after_t1_without_changing_t1_score():
    config = StrategyTunablesConfig()
    start = datetime(2026, 9, 21, 4, 15, tzinfo=UTC)
    trigger_bar = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    t1_bar = _bar(
        start + timedelta(minutes=15),
        open_=101.5,
        high=104.1,
        low=100.5,
        close=103.5,
    )
    runner_bar = _bar(
        start + timedelta(minutes=30),
        open_=103.5,
        high=106.1,
        low=103.0,
        close=105.5,
    )
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[trigger_bar, t1_bar, runner_bar],
        future_features={trigger_bar.end_time: _feature(trigger_bar)},
        config=config,
    )

    assert label["hit_t1_before_stop"] is True
    assert label["hit_runner_before_stop"] is True
    assert label["t1_first_hit_r"] == config.t1_r


def test_walk_forward_folds_are_strictly_chronological_and_non_overlapping_within_fold():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    folds = _walk_forward_folds(dates, train_sessions=6, test_sessions=3)

    assert len(folds) == 2
    first_train, first_test = folds[0]
    second_train, second_test = folds[1]

    assert first_train == dates[:6]
    assert first_test == dates[6:9]
    assert set(first_train).isdisjoint(first_test)
    assert second_train == dates[3:9]
    assert second_test == dates[9:12]
    assert set(second_train).isdisjoint(second_test)


def test_research_label_rejects_trigger_after_entry_window():
    config = StrategyTunablesConfig()
    # 14:45 IST is 09:15 UTC. A setup formed at 14:45 can only be tested by
    # later bars; a 15:00 trigger must not be credited as a Strategy A entry.
    start = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)
    after_close = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[after_close],
        future_features={after_close.end_time: _feature(after_close)},
        config=config,
    )

    assert after_close.end_time.hour == 9
    assert after_close.end_time.minute == 30
    assert label["trigger_status"] == "ENTRY_SESSION_CLOSED"
    assert label["entry_price"] is None


def test_entry_timing_enrichment_is_backward_looking_and_measures_momentum_decay():
    components = {
        "ema_order": True,
        "di_direction": True,
        "adx": True,
        "ema_separation": True,
        "confirmation_direction": True,
        "confirmation_body": True,
        "confirmation_close_location": True,
        "confirmation_range": True,
        "sr_present": True,
        "sr_touch": True,
        "ema_or_vwap_near": True,
        "minimum_stop_distance": True,
        "maximum_stop_distance": True,
        "opposing_sr_room": True,
    }
    first = {
        **_research_row(),
        "timestamp": "2026-09-21T05:00:00+00:00",
        "close": 101.0,
        "ema20": 100.0,
        "session_vwap": 99.5,
        "atr14": 2.0,
        "adx14": 26.0,
        "plus_di14": 35.0,
        "minus_di14": 15.0,
        "baseline_components": components,
        "baseline_pass": True,
    }
    second = {
        **first,
        "timestamp": "2026-09-21T05:15:00+00:00",
        "close": 101.2,
        "ema20": 100.1,
        "adx14": 24.0,
        "plus_di14": 30.0,
        "minus_di14": 18.0,
        "label": {
            **first["label"],
            "trigger_timestamp": "2026-09-21T05:30:00+00:00",
        },
    }

    enriched = enrich_rows([first, second])
    later = next(row for row in enriched if row["timestamp"].endswith("05:15:00+00:00"))
    timing = later["timing"]

    assert timing["adx_delta_1bar"] == -2.0
    assert timing["directional_di_spread_delta_1bar"] < 0
    assert timing["ema20_directional_slope_atr_1bar"] > 0
    assert timing["trend_streak_bars"] == 2
    assert timing["trigger_delay_bars"] == 1


def test_entry_timing_report_keeps_pre_risk_and_baseline_cohorts_separate():
    row = {
        **_research_row(),
        "close": 101.0,
        "ema20": 100.0,
        "session_vwap": 99.5,
        "atr14": 2.0,
        "adx14": 25.0,
        "plus_di14": 30.0,
        "minus_di14": 10.0,
        "baseline_components": {
            "ema_order": True,
            "di_direction": True,
            "adx": True,
            "ema_separation": True,
            "confirmation_direction": True,
            "confirmation_body": True,
            "confirmation_close_location": True,
            "confirmation_range": True,
            "sr_present": True,
            "sr_touch": True,
            "ema_or_vwap_near": True,
            "minimum_stop_distance": False,
            "maximum_stop_distance": True,
            "opposing_sr_room": False,
        },
        "baseline_pass": False,
    }
    report = build_entry_timing_report({"source": "BREEZE", "rows": [row]})

    assert report["cohorts"]["pre_risk"]["metrics"]["candidate_rows"] == 1
    assert report["cohorts"]["baseline_ready"]["metrics"]["candidate_rows"] == 0
    assert "adx_change_1bar" in report["cohorts"]["pre_risk"]["dimensions"]
    assert "trigger_delay" in report["cohorts"]["pre_risk"]["dimensions"]


def test_v3_candidate_catalog_keeps_baseline_and_momentum_hypotheses_separate():
    names = [candidate.name for candidate in candidate_catalog()]
    assert names[0] == "baseline_v2"
    assert "without_adx_floor" in names
    assert "without_adx_reject_sharp_decay" in names
    assert "without_adx_controlled_fade" in names
    assert "without_adx_moderate_ema_slope" in names
    assert "confirmation_close_location_20" in names


def test_v3_walk_forward_can_abstain_instead_of_selecting_negative_train_variant():
    rows = []
    base_components = {
        "ema_order": True,
        "di_direction": True,
        "adx": True,
        "ema_separation": True,
        "confirmation_direction": True,
        "confirmation_body": True,
        "confirmation_close_location": True,
        "confirmation_range": True,
        "sr_present": True,
        "sr_touch": True,
        "ema_or_vwap_near": True,
        "minimum_stop_distance": True,
        "maximum_stop_distance": True,
        "opposing_sr_room": True,
    }
    # Six sessions: first four train, last two test. Every triggered train row
    # loses, so a selector that requires positive train expectancy must abstain.
    for day in range(1, 7):
        rows.append({
            "date": f"2026-01-{day:02d}",
            "direction": "CALL",
            "adx14": 25.0,
            "confirmation_close_location_pct": 0.1,
            "baseline_components": base_components,
            "baseline_pass": True,
            "timing": {
                "adx_delta_2bars": -1.0,
                "ema20_directional_slope_atr_1bar": 0.1,
            },
            "label": {
                "trigger_status": "TRIGGERED",
                "t1_first_hit_r": -1.0,
                "max_favorable_r": 0.2,
                "max_adverse_r": 1.0,
                "hit_t1_before_stop": False,
            },
        })

    report = build_v3_candidate_report(
        {"enriched_rows": rows},
        train_sessions=4,
        test_sessions=2,
        min_train_triggers=2,
    )

    assert report["fold_count"] == 1
    assert report["folds"][0]["selection"] == "ABSTAIN"
    assert report["adaptive_selection_out_of_sample"]["triggered_rows"] == 0


def test_v3_momentum_context_uses_true_previous_bars_and_directional_slope():
    current = SimpleNamespace(adx14=24.0, ema20=101.0, atr14=2.0)
    previous = SimpleNamespace(adx14=25.0, ema20=100.8, atr14=2.0)
    previous2 = SimpleNamespace(adx14=27.0, ema20=100.5, atr14=2.0)

    call_ctx = _momentum_context(current, previous, previous2, StrategyDirection.CALL)
    put_ctx = _momentum_context(current, previous, previous2, StrategyDirection.PUT)

    assert call_ctx["adx_delta_2bars"] == -3.0
    assert call_ctx["ema20_directional_slope_atr_1bar"] == pytest.approx(0.1)
    assert put_ctx["ema20_directional_slope_atr_1bar"] == pytest.approx(-0.1)


def test_v3_guard_requires_non_collapsing_adx_and_non_steep_directional_slope():
    row = _research_row()
    row["baseline_components"] = {
        "ema_order": True,
        "di_direction": True,
        "adx": False,
        "ema_separation": True,
        "confirmation_direction": True,
        "confirmation_body": True,
        "confirmation_close_location": True,
        "confirmation_range": True,
        "sr_present": True,
        "sr_touch": True,
        "ema_or_vwap_near": True,
        "minimum_stop_distance": True,
        "maximum_stop_distance": True,
        "opposing_sr_room": True,
    }
    row["momentum_context"] = {
        "adx_delta_2bars": -1.5,
        "ema20_directional_slope_atr_1bar": 0.10,
    }
    row["timestamp"] = "2026-09-21T05:00:00+00:00"

    assert _variant_pass(row, "guard_d2_m2_slope_0_015") is True

    row["momentum_context"]["adx_delta_2bars"] = -2.1
    assert _variant_pass(row, "guard_d2_m2_slope_0_015") is False

    row["momentum_context"]["adx_delta_2bars"] = -1.5
    row["momentum_context"]["ema20_directional_slope_atr_1bar"] = 0.16
    assert _variant_pass(row, "guard_d2_m2_slope_0_015") is False
