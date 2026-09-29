import numpy as np
import pandas as pd

from services.historical.independent_short_swing_futures_spot_dislocation_options_findings import (
    EXPECTED_OPTION_SHA256,
    _atm_strike,
    _cell_pass,
    _cost_points,
    _signals,
)
from services.historical.independent_short_swing_futures_spot_dislocation_options_protocol import (
    COST_MODEL,
)


def test_exact_option_source_hashes_are_locked():
    assert EXPECTED_OPTION_SHA256["cohort1"] == (
        "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
    )
    assert EXPECTED_OPTION_SHA256["cohort2"] == (
        "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc"
    )


def test_atm_rounding_is_deterministic_half_up():
    assert _atm_strike(22024.9) == 22000
    assert _atm_strike(22025.0) == 22050
    assert _atm_strike(22075.0) == 22100


def test_option_cost_points_matches_frozen_charge_formula():
    entry = np.array([100.0])
    exit_ = np.array([110.0])
    actual = float(_cost_points(entry, exit_)[0])
    lot = COST_MODEL["lot_size"]
    brokerage = 40.0 / lot
    stt = 110.0 * COST_MODEL["options_stt_sell_premium_rate"]
    exchange = 210.0 * COST_MODEL["options_exchange_transaction_rate_each_side"]
    sebi = 210.0 * COST_MODEL["sebi_turnover_rate_each_side"]
    stamp = 100.0 * COST_MODEL["options_stamp_buy_rate"]
    gst = COST_MODEL["gst_rate"] * (brokerage + exchange + sebi)
    assert np.isclose(actual, brokerage + stt + exchange + sebi + stamp + gst)


def test_cell_pass_requires_cost_robustness_and_stability():
    summaries = {
        "1.0": {
            "cohort1_net_mean_points": 1.0,
            "cohort2_net_mean_points": 0.8,
            "positive_chronological_blocks": 10,
            "session_cluster_bootstrap_net_mean_95pct_points": [0.1, 2.0],
        },
        "2.0": {"pooled_net_mean_points": 0.2},
    }
    passed, failures = _cell_pass(summaries)
    assert passed is True
    assert failures == []

    summaries["2.0"]["pooled_net_mean_points"] = -0.1
    passed, failures = _cell_pass(summaries)
    assert passed is False
    assert "two_point_slippage_pooled_not_positive" in failures


def test_option_signals_require_same_scorable_horizon_as_structural_screen():
    frame = pd.DataFrame(
        {
            "date": ["2025-09-09", "2025-09-09"],
            "entry_timestamp": pd.to_datetime(
                ["2025-09-09 15:25:00", "2025-09-09 15:30:00"]
            ),
            "abs_return_dislocation_bps": [10.0, 10.0],
            "direction": [-1.0, -1.0],
            "options_specific_fast_lead": [-1.0, -1.0],
            "h5m_terminal_bps": [1.0, np.nan],
        }
    )
    selected = _signals(frame, percentile=80, horizon=5, threshold=2.0)
    assert list(selected["entry_timestamp"].dt.strftime("%H:%M")) == ["15:25"]
