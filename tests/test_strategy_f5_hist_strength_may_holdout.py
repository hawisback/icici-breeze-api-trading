from services.historical.strategy_f5_hist_strength_may_holdout import (
    _candidate_observations,
    _holdout_report,
)
from services.historical.strategy_f5_hist_strength_may_holdout_protocol import (
    CANDIDATE_NAME,
    ENTRY,
    EXPIRIES,
    PROTOCOL_VERSION,
    VALIDATION_GATE,
)


def test_may_holdout_is_frozen_single_candidate():
    assert PROTOCOL_VERSION == "STRATEGY_F5_HIST_STRENGTH_MAY_HOLDOUT_V1"
    assert CANDIDATE_NAME == "F5_HIST_PCT_GE_0_15"
    assert ENTRY["histogram_strength_pct_threshold"] == 0.15
    assert ENTRY["rvi_threshold"] == 50.0
    assert EXPIRIES == [
        "2026-05-05",
        "2026-05-12",
        "2026-05-19",
        "2026-05-26",
        "2026-06-02",
    ]
    assert VALIDATION_GATE["minimum_baseline_activated_entry_capture_pct"] == 60.0


def test_candidate_filter_only_suppresses_weak_bullish_entries():
    observations = {
        "2026-05-04": {
            "2026-05-04T10:01:00+05:30": {
                "CE": {
                    "bullish_cross": True,
                    "bearish_cross": False,
                    "macd_hist_pct": 0.14,
                },
                "PE": {
                    "bullish_cross": True,
                    "bearish_cross": False,
                    "macd_hist_pct": 0.16,
                },
            },
            "2026-05-04T10:03:00+05:30": {
                "CE": {
                    "bullish_cross": False,
                    "bearish_cross": True,
                    "macd_hist_pct": -0.20,
                }
            },
        }
    }

    filtered = _candidate_observations(observations)

    assert filtered["2026-05-04"]["2026-05-04T10:01:00+05:30"]["CE"][
        "bullish_cross"
    ] is False
    assert filtered["2026-05-04"]["2026-05-04T10:01:00+05:30"]["PE"][
        "bullish_cross"
    ] is True
    assert filtered["2026-05-04"]["2026-05-04T10:03:00+05:30"]["CE"][
        "bearish_cross"
    ] is True


def _trade(month, right, pnl):
    return {
        "month": month,
        "right": right,
        "exit_reason": "PRE_TRAIL_BEARISH_MACD_CROSS",
        "hold_minutes": 4,
        "trail_activated": False,
        "slippage_sensitivity": {
            "0.00": {"net_pnl_inr": pnl},
            "0.50": {"net_pnl_inr": pnl - 65.0},
            "1.00": {"net_pnl_inr": pnl - 130.0},
        },
    }


def test_holdout_report_uses_may_not_development_months():
    report = _holdout_report(
        [
            _trade("2026-05", "CE", 100.0),
            _trade("2026-05", "PE", -50.0),
        ],
        [],
    )
    assert list(report["by_month"]) == ["2026-05"]
    assert report["by_month"]["2026-05"]["0.00"]["trades"] == 2
    assert report["pooled"]["0.00"]["net_pnl_inr"] == 50.0
