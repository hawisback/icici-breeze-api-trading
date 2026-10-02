from services.historical.strategy_f5_four_factor_quality_score import (
    _adjacent_monotonicity,
    _matched_rows,
)
from services.historical.strategy_f5_four_factor_quality_score_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    SCORE_COMPONENTS,
    SCORE_RANGE,
)


def test_quality_score_protocol_is_matched_entry_only():
    assert PROTOCOL_VERSION == "STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_V1"
    assert SCORE_COMPONENTS == [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "STOCH_NOT_OVERBOUGHT",
        "HIST_STRONG",
    ]
    assert SCORE_RANGE == [0, 1, 2, 3, 4]
    assert GUARDRAILS["matched_entry_only"] is True
    assert GUARDRAILS["keep_all_original_f5_trades"] is True
    assert GUARDRAILS["no_score_cutoff_selection"] is True
    assert GUARDRAILS["no_score_weight_optimization"] is True
    assert GUARDRAILS["live_execution"] is False


def test_matched_rows_score_is_sum_of_four_boolean_components():
    trade = {
        "date": "2026-07-01",
        "month": "2026-07",
        "entry_timestamp": "2026-07-01T10:01:00+05:30",
        "expiry": "2026-07-07",
        "strike": 25000,
        "right": "CE",
        "entry_open": 100.0,
        "trail_activated": True,
        "primary_cost_model": {"net_pnl_inr": 500.0},
    }
    key = (
        "2026-07-01T10:01:00+05:30",
        "2026-07-07",
        25000,
        "CE",
    )
    lookup = {
        key: {
            "ATR_ACTIVE": True,
            "BB_ACTIVE": False,
            "STOCH_NOT_OVERBOUGHT": True,
            "HIST_STRONG": True,
            "atr14_pct": 5.0,
            "atr14_prior20_median": 4.0,
            "bb_bandwidth20_pct": 10.0,
            "bb_prior20_median": 12.0,
            "stoch_d3": 60.0,
            "macd_hist_pct": 0.2,
        }
    }

    rows, missing = _matched_rows([trade], lookup)

    assert missing == []
    assert rows[0]["quality_score"] == 3
    assert rows[0]["bad_trade"] is False
    assert rows[0]["baseline_winner"] is True
    assert rows[0]["trail_activated"] is True


def test_bad_trade_definition_requires_loss_and_no_activation():
    base = {
        "date": "2026-07-01",
        "month": "2026-07",
        "expiry": "2026-07-07",
        "strike": 25000,
        "right": "PE",
        "entry_open": 100.0,
    }
    features = {
        "ATR_ACTIVE": False,
        "BB_ACTIVE": False,
        "STOCH_NOT_OVERBOUGHT": False,
        "HIST_STRONG": False,
        "atr14_pct": 3.0,
        "atr14_prior20_median": 4.0,
        "bb_bandwidth20_pct": 5.0,
        "bb_prior20_median": 8.0,
        "stoch_d3": 90.0,
        "macd_hist_pct": 0.1,
    }
    trades = [
        {
            **base,
            "entry_timestamp": "2026-07-01T10:01:00+05:30",
            "trail_activated": False,
            "primary_cost_model": {"net_pnl_inr": -100.0},
        },
        {
            **base,
            "entry_timestamp": "2026-07-01T10:03:00+05:30",
            "trail_activated": True,
            "primary_cost_model": {"net_pnl_inr": -100.0},
        },
    ]
    lookup = {
        (
            trade["entry_timestamp"],
            trade["expiry"],
            trade["strike"],
            trade["right"],
        ): features
        for trade in trades
    }

    rows, missing = _matched_rows(trades, lookup)

    assert missing == []
    assert rows[0]["bad_trade"] is True
    assert rows[1]["bad_trade"] is False


def test_adjacent_monotonicity_detects_pass_and_violation():
    table = {
        "0": {"trades": 10, "bad_trade_rate_pct": 80.0},
        "1": {"trades": 10, "bad_trade_rate_pct": 70.0},
        "2": {"trades": 10, "bad_trade_rate_pct": 60.0},
        "3": {"trades": 10, "bad_trade_rate_pct": 65.0},
        "4": {"trades": 10, "bad_trade_rate_pct": 50.0},
    }
    result = _adjacent_monotonicity(
        table,
        "bad_trade_rate_pct",
        "NON_INCREASING_WITH_SCORE",
    )
    assert result["violations"] == 1
    assert result["passed_all_adjacent_comparisons"] is False
