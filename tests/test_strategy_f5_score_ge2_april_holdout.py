from services.historical.strategy_f5_score_ge2_april_holdout import (
    _score,
)
from services.historical.strategy_f5_score_ge2_april_holdout_protocol import (
    CANDIDATE_NAME,
    ENTRY,
    EXPIRIES,
    GUARDRAILS,
    PROTOCOL_VERSION,
    VALIDATION_GATE,
)


def test_april_score_holdout_is_frozen():
    assert PROTOCOL_VERSION == "STRATEGY_F5_SCORE_GE2_APRIL_HOLDOUT_V1"
    assert CANDIDATE_NAME == "F5_QUALITY_SCORE_GE_2"
    assert ENTRY["quality_score_min"] == 2
    assert ENTRY["score_components"] == [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "STOCH_NOT_OVERBOUGHT",
        "HIST_STRONG",
    ]
    assert EXPIRIES == [
        "2026-04-07",
        "2026-04-13",
        "2026-04-21",
        "2026-04-28",
        "2026-05-05",
    ]
    assert VALIDATION_GATE["minimum_baseline_activated_signal_capture_pct"] == 65.0
    assert VALIDATION_GATE["minimum_baseline_winner_signal_capture_pct"] == 65.0
    assert GUARDRAILS["no_score_cutoff_search_on_april"] is True
    assert GUARDRAILS["keep_existing_f5_trail_unchanged"] is True


def test_score_counts_only_true_components():
    features = {
        "ATR_ACTIVE": True,
        "BB_ACTIVE": False,
        "STOCH_NOT_OVERBOUGHT": True,
        "HIST_STRONG": False,
    }
    assert _score(features) == 2
    assert _score(None) is None
