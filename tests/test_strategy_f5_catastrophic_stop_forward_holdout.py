from services.historical.strategy_f5_catastrophic_stop_forward_holdout import (
    analyze,
)
from services.historical.strategy_f5_catastrophic_stop_forward_holdout_protocol import (
    FROZEN_ON,
    FROZEN_STOP_DISTANCE_PCT,
    GUARDRAILS,
)


def test_forward_stop_is_frozen_before_window():
    assert FROZEN_ON == "2026-10-02"
    assert FROZEN_STOP_DISTANCE_PCT == 27.95
    assert GUARDRAILS["no_stop_retuning"] is True
    assert GUARDRAILS["no_30_60_candidate"] is True


def test_partial_forward_market_scores_no_outcomes():
    payload = {
        "protocol_version": "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_V1",
        "strategy_outcomes_scored": False,
        "daily_contracts": [
            {"date": "2026-10-05"},
            {"date": "2026-10-06"},
        ],
    }
    result = analyze(payload, source_sha256="abc")
    assert result["decision"] == "HOLDOUT_NOT_COMPLETE_NO_OUTCOMES_SCORED"
    assert "reports" not in result
