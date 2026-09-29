import pandas as pd

from services.historical.independent_short_swing_external_open_close_intraday_momentum_findings import (
    _align,
    _frame,
    _passes,
    _signal_trades,
)
from services.historical.independent_short_swing_external_open_close_intraday_momentum_protocol import (
    GUARDRAILS,
    PRIMARY_RULE,
    SOURCE_EVENT_SHA256,
    STRUCTURAL_GATE,
)


def _session_rows(cohort, block, day, opening_30m_return, h30_terminal):
    base = pd.Timestamp(f"{day}T09:15:00+05:30")
    rows = []
    for i in range(75):
        ts = base + pd.Timedelta(minutes=5 * i)
        net6 = None if i < 5 else 1.0
        if i == 5:
            net6 = float(opening_30m_return)
        rows.append({
            "cohort": cohort,
            "cohort_block": block,
            "timestamp": ts.isoformat(),
            "date": day,
            "net_return_6_bps": net6,
            "entry_timestamp": (ts + pd.Timedelta(minutes=5)).isoformat(),
            "entry_gap_bps": 0.0,
            "h30m_terminal_bps": (
                float(h30_terminal) if i == 68 else None
            ),
            "h30m_long_mfe_bps": 4.0 if i == 68 else None,
            "h30m_long_mae_bps": -2.0 if i == 68 else None,
            "h30m_short_mfe_bps": 2.0 if i == 68 else None,
            "h30m_short_mae_bps": -4.0 if i == 68 else None,
        })
    return rows


def _payload():
    rows = []
    rows += _session_rows("cohort1", 1, "2026-01-02", 5.0, 2.0)
    rows += _session_rows("cohort1", 1, "2026-01-05", -4.0, -3.0)
    return {"events": rows}


def test_external_protocol_pins_v2_and_one_exact_formulation():
    assert SOURCE_EVENT_SHA256 == (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    )
    assert PRIMARY_RULE["signal_row_time"] == "09:40"
    assert PRIMARY_RULE["trade_row_time"] == "14:55"
    assert PRIMARY_RULE["entry_time"] == "15:00 one-minute futures open"
    assert PRIMARY_RULE["exit_time"] == "15:29 one-minute futures close"
    assert PRIMARY_RULE["holding_minutes"] == 30
    assert PRIMARY_RULE["signal_magnitude_threshold"] is None
    assert GUARDRAILS["external_hypothesis_path"] is True
    assert GUARDRAILS["single_primary_rule_only"] is True
    assert GUARDRAILS["no_alternate_entry_time"] is True
    assert GUARDRAILS["no_alternate_exit_time"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_signal_uses_0940_opening_half_hour_and_1455_trade_row():
    trades = _signal_trades(_frame(_payload()))
    assert len(trades) == 2

    first = trades.loc[trades["date"] == "2026-01-02"].iloc[0]
    second = trades.loc[trades["date"] == "2026-01-05"].iloc[0]

    assert first["opening_half_hour_return_bps"] == 5.0
    assert first["direction"] == 1.0
    assert first["timestamp"].strftime("%H:%M") == "14:55"
    assert first["entry_timestamp"].strftime("%H:%M") == "15:00"

    assert second["opening_half_hour_return_bps"] == -4.0
    assert second["direction"] == -1.0
    assert second["timestamp"].strftime("%H:%M") == "14:55"


def test_zero_opening_return_produces_no_trade():
    payload = {
        "events": _session_rows(
            "cohort1", 1, "2026-01-02", 0.0, 2.0
        )
    }
    trades = _signal_trades(_frame(payload))
    assert trades.empty


def test_aligned_outcome_uses_opening_half_hour_direction():
    trades = _align(_signal_trades(_frame(_payload())))
    first = trades.loc[trades["date"] == "2026-01-02"].iloc[0]
    second = trades.loc[trades["date"] == "2026-01-05"].iloc[0]

    assert first["aligned_terminal_bps"] == 2.0
    assert second["aligned_terminal_bps"] == 3.0


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

    summary["cohort_mean_bps"]["cohort2"] = -0.01
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort2_mean_not_positive" in failures


def test_external_structural_gate_is_frozen():
    assert STRUCTURAL_GATE["minimum_pooled_trades"] == 150
    assert STRUCTURAL_GATE["minimum_trades_each_cohort"] == 40
    assert STRUCTURAL_GATE["minimum_positive_chronological_blocks"] == 15
    assert STRUCTURAL_GATE["total_chronological_blocks"] == 22
    assert STRUCTURAL_GATE["bootstrap_samples"] == 10000


def test_fixture_uses_real_v2_h30m_schema_names():
    row = _session_rows("cohort1", 1, "2026-01-02", 1.0, 2.0)[68]
    assert "h30m_terminal_bps" in row
    assert "h30m_long_mfe_bps" in row
    assert "h30m_long_mae_bps" in row
    assert "h30m_short_mfe_bps" in row
    assert "h30m_short_mae_bps" in row
    assert "h30_terminal_bps" not in row
