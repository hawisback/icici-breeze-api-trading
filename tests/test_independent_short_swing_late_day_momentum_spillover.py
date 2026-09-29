import pandas as pd

from services.historical.independent_short_swing_late_day_momentum_spillover_findings import (
    _frame,
    _passes,
    _select,
    _signal_rows,
)
from services.historical.independent_short_swing_late_day_momentum_spillover_protocol import (
    GUARDRAILS,
    SOURCE_EVENT_SHA256,
    STRUCTURAL_GATE,
)


def _session_rows(cohort, block, day, final_net_return):
    base = pd.Timestamp(f"{day}T09:15:00+05:30")
    rows = []
    for i in range(75):
        ts = base + pd.Timedelta(minutes=5 * i)
        net6 = None if i < 5 else float(i)
        if i == 74:
            net6 = float(final_net_return)
        rows.append({
            "cohort": cohort,
            "cohort_block": block,
            "timestamp": ts.isoformat(),
            "date": day,
            "net_return_6_bps": net6,
            "entry_timestamp": (ts + pd.Timedelta(minutes=5)).isoformat(),
            "entry_gap_bps": 0.0,
            "h5m_terminal_bps": 2.0,
            "h5m_long_mfe_bps": 3.0,
            "h5m_long_mae_bps": -1.0,
            "h5m_short_mfe_bps": 1.0,
            "h5m_short_mae_bps": -3.0,
            "h10m_terminal_bps": 2.0,
            "h10m_long_mfe_bps": 3.0,
            "h10m_long_mae_bps": -1.0,
            "h10m_short_mfe_bps": 1.0,
            "h10m_short_mae_bps": -3.0,
            "h15m_terminal_bps": 2.0,
            "h15m_long_mfe_bps": 3.0,
            "h15m_long_mae_bps": -1.0,
            "h15m_short_mfe_bps": 1.0,
            "h15m_short_mae_bps": -3.0,
            "h30m_terminal_bps": 2.0,
            "h30m_long_mfe_bps": 3.0,
            "h30m_long_mae_bps": -1.0,
            "h30m_short_mfe_bps": 1.0,
            "h30m_short_mae_bps": -3.0,
        })
    return rows


def _payload():
    rows = []
    rows += _session_rows("cohort1", 1, "2026-01-02", 5.0)
    rows += _session_rows("cohort1", 1, "2026-01-05", -4.0)
    rows += _session_rows("cohort2", 1, "2025-12-22", -3.0)
    rows += _session_rows("cohort2", 1, "2025-12-23", 6.0)
    return {"events": rows}


def test_protocol_pins_v2_and_forbids_rescue_filters():
    assert SOURCE_EVENT_SHA256 == (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    )
    assert GUARDRAILS["single_primary_rule_only"] is True
    assert GUARDRAILS["cross_cohort_carry_forbidden"] is True
    assert GUARDRAILS["no_magnitude_threshold_search"] is True
    assert GUARDRAILS["no_opening_gap_filter"] is True
    assert GUARDRAILS["no_post_hoc_reversal_test"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_signal_uses_prior_session_final_30m_within_same_cohort_only():
    frame = _frame(_payload())
    signals = _signal_rows(frame)

    assert len(signals) == 2
    c1 = signals.loc[signals["cohort"] == "cohort1"].iloc[0]
    c2 = signals.loc[signals["cohort"] == "cohort2"].iloc[0]

    assert c1["date"] == "2026-01-05"
    assert c1["prior_session_final_net_return_6_bps"] == 5.0
    assert c1["direction"] == 1.0
    assert c1["timestamp"].strftime("%H:%M") == "09:15"
    assert c1["entry_timestamp"].strftime("%H:%M") == "09:20"

    assert c2["date"] == "2025-12-23"
    assert c2["prior_session_final_net_return_6_bps"] == -3.0
    assert c2["direction"] == -1.0


def test_zero_prior_late_day_return_creates_no_next_session_signal():
    payload = {
        "events": (
            _session_rows("cohort1", 1, "2026-01-02", 0.0)
            + _session_rows("cohort1", 1, "2026-01-05", 4.0)
        )
    }
    signals = _signal_rows(_frame(payload))
    assert signals.empty


def test_aligned_outcome_uses_prior_session_direction():
    signals = _signal_rows(_frame(_payload()))
    selected = _select(signals, horizon_minutes=5)

    long_row = selected.loc[selected["cohort"] == "cohort1"].iloc[0]
    short_row = selected.loc[selected["cohort"] == "cohort2"].iloc[0]
    assert long_row["aligned_terminal_bps"] == 2.0
    assert short_row["aligned_terminal_bps"] == -2.0


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


def test_spillover_structural_gate_is_frozen():
    assert STRUCTURAL_GATE["minimum_pooled_trades"] == 150
    assert STRUCTURAL_GATE["minimum_trades_each_cohort"] == 40
    assert STRUCTURAL_GATE["minimum_positive_chronological_blocks"] == 15
    assert STRUCTURAL_GATE["total_chronological_blocks"] == 22
    assert STRUCTURAL_GATE["bootstrap_samples"] == 10000
