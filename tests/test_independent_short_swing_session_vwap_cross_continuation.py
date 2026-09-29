import pandas as pd

from services.historical.independent_short_swing_session_vwap_cross_continuation_findings import (
    _frame,
    _passes,
    _select,
)
from services.historical.independent_short_swing_session_vwap_cross_continuation_protocol import (
    GUARDRAILS,
    PRIMARY_RULE,
    SOURCE_EVENT_SHA256,
    STRUCTURAL_GATE,
)


def _payload():
    base = pd.Timestamp("2026-01-02T09:15:00+05:30")
    rows = []
    closes = [100.0, 99.5, 99.0, 98.8, 98.9, 100.5, 100.8]
    volumes = [100.0] * len(closes)
    for i, close in enumerate(closes):
        ts = base + pd.Timedelta(minutes=5 * i)
        rows.append({
            "cohort": "cohort1",
            "cohort_block": 1,
            "timestamp": ts.isoformat(),
            "date": "2026-01-02",
            "futures_high": close + 0.5,
            "futures_low": close - 0.5,
            "futures_close": close,
            "futures_volume": volumes[i],
            "entry_timestamp": (ts + pd.Timedelta(minutes=5)).isoformat(),
            "entry_gap_bps": 0.0,
            "h5m_terminal_bps": 1.0,
            "h5m_long_mfe_bps": 2.0,
            "h5m_long_mae_bps": -1.0,
            "h5m_short_mfe_bps": 1.0,
            "h5m_short_mae_bps": -2.0,
            "h10m_terminal_bps": 1.0,
            "h10m_long_mfe_bps": 2.0,
            "h10m_long_mae_bps": -1.0,
            "h10m_short_mfe_bps": 1.0,
            "h10m_short_mae_bps": -2.0,
            "h15m_terminal_bps": 1.0,
            "h15m_long_mfe_bps": 2.0,
            "h15m_long_mae_bps": -1.0,
            "h15m_short_mfe_bps": 1.0,
            "h15m_short_mae_bps": -2.0,
            "h30m_terminal_bps": 1.0,
            "h30m_long_mfe_bps": 2.0,
            "h30m_long_mae_bps": -1.0,
            "h30m_short_mfe_bps": 1.0,
            "h30m_short_mae_bps": -2.0,
        })
    return {"events": rows}


def test_protocol_pins_v2_and_one_rule():
    assert SOURCE_EVENT_SHA256 == (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    )
    assert PRIMARY_RULE["minimum_completed_bars"] == 6
    assert PRIMARY_RULE["distance_threshold"] is None
    assert PRIMARY_RULE["volume_threshold"] is None
    assert PRIMARY_RULE["options_fast_lead_filter"] == "off"
    assert PRIMARY_RULE["OI_filter"] == "off"
    assert GUARDRAILS["single_primary_rule_only"] is True
    assert GUARDRAILS["bar_vwap_proxy_not_exact_tick_vwap"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_vwap_cross_requires_six_completed_bars():
    frame = _frame(_payload())
    assert frame.loc[:4, "direction"].eq(0.0).all()
    assert frame.loc[5, "session_bar_number"] == 6
    assert frame.loc[5, "direction"] == 1.0
    assert bool(frame.loc[5, "cross_up"]) is True


def test_cross_direction_is_used_for_aligned_outcome():
    frame = _frame(_payload())
    selected = _select(frame, horizon_minutes=5)
    assert len(selected) == 1
    row = selected.iloc[0]
    assert row["direction"] == 1.0
    assert row["aligned_terminal_bps"] == 1.0


def test_structural_gate_requires_all_three_cohorts_and_15_of_22_blocks():
    summary = {
        "trades": 200,
        "trades_by_cohort": {"cohort1": 60, "cohort2": 60, "cohort3": 80},
        "cohort_mean_bps": {"cohort1": 0.1, "cohort2": 0.1, "cohort3": 0.1},
        "positive_chronological_blocks": 15,
        "session_cluster_bootstrap_mean_95pct_bps": [0.01, 0.2],
    }
    passed, failures = _passes(summary)
    assert passed is True
    assert failures == []

    summary["positive_chronological_blocks"] = 14
    passed, failures = _passes(summary)
    assert passed is False
    assert "chronological_block_stability" in failures


def test_vwap_structural_gate_is_frozen():
    assert STRUCTURAL_GATE["minimum_pooled_trades"] == 150
    assert STRUCTURAL_GATE["minimum_trades_each_cohort"] == 40
    assert STRUCTURAL_GATE["minimum_positive_chronological_blocks"] == 15
    assert STRUCTURAL_GATE["total_chronological_blocks"] == 22
    assert STRUCTURAL_GATE["bootstrap_samples"] == 10000
