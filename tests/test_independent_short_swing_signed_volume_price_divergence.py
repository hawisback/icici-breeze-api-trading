import numpy as np
import pandas as pd

from services.historical.independent_short_swing_signed_volume_price_divergence_findings import (
    _frame,
    _passes,
    _select,
)
from services.historical.independent_short_swing_signed_volume_price_divergence_protocol import (
    GUARDRAILS,
    PRIMARY_RULE,
    SOURCE_EVENT_SHA256,
    STRUCTURAL_GATE,
)


def _payload():
    base = pd.Timestamp("2026-01-02T09:15:00+05:30")
    rows = []
    # Returns become +, +, - while signed volume remains strongly positive and
    # three-bar net price response is negative at the fourth event.
    closes = [100.0, 101.0, 102.0, 99.0, 100.0, 101.0]
    volumes = [100.0, 500.0, 500.0, 100.0, 100.0, 100.0]
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        ts = base + pd.Timedelta(minutes=5 * i)
        ret = None if i == 0 else (close / closes[i - 1] - 1.0) * 10000.0
        rows.append({
            "cohort": "cohort1",
            "cohort_block": 1,
            "timestamp": ts.isoformat(),
            "date": "2026-01-02",
            "futures_return_bps": ret,
            "futures_volume": volume,
            "net_return_3_bps": (
                None if i < 2 else (close / closes[i - 2] - 1.0) * 10000.0
            ),
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


def test_protocol_pins_corrected_v2_hash_and_single_rule():
    assert SOURCE_EVENT_SHA256 == (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    )
    assert PRIMARY_RULE["abs_signed_volume_imbalance_3_min"] == 1.0 / 3.0
    assert PRIMARY_RULE["options_fast_lead_filter"] == "off"
    assert PRIMARY_RULE["OI_filter"] == "off"
    assert GUARDRAILS["single_primary_rule_only"] is True
    assert GUARDRAILS["no_alternate_imbalance_thresholds"] is True
    assert GUARDRAILS["no_post_hoc_direction_flip"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_signed_volume_feature_requires_three_return_bearing_bars():
    frame = _frame(_payload())
    assert frame.loc[:2, "signed_volume_imbalance_3"].isna().all()
    assert pd.notna(frame.loc[3, "signed_volume_imbalance_3"])

    # At index 3, bars 1..3 have signed volume +500 +500 -100 over 1100.
    assert frame.loc[3, "signed_volume_imbalance_3"] == np.float64(900.0 / 1100.0)
    assert bool(frame.loc[3, "divergence"]) is True
    assert frame.loc[3, "direction"] == 1.0


def test_primary_rule_selects_only_frozen_divergence_direction():
    frame = _frame(_payload())
    selected = _select(frame, horizon_minutes=5)
    assert len(selected) >= 1
    row = selected.loc[
        selected["timestamp"] == pd.Timestamp("2026-01-02T09:30:00+05:30")
    ].iloc[0]
    assert row["direction"] == 1.0
    assert row["aligned_terminal_bps"] == 1.0
    assert row["abs_signed_volume_imbalance_3"] >= 1.0 / 3.0


def test_structural_gate_requires_all_three_cohorts_and_block_stability():
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

    summary["cohort_mean_bps"]["cohort3"] = -0.01
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort3_mean_not_positive" in failures


def test_structural_gate_is_frozen_for_22_blocks():
    assert STRUCTURAL_GATE["minimum_pooled_trades"] == 150
    assert STRUCTURAL_GATE["minimum_trades_each_cohort"] == 40
    assert STRUCTURAL_GATE["minimum_positive_chronological_blocks"] == 15
    assert STRUCTURAL_GATE["total_chronological_blocks"] == 22
    assert STRUCTURAL_GATE["bootstrap_samples"] == 10000
