from services.historical.strategy_f5_dominant_nifty_regime_jul_sep import (
    _status,
    _summary,
)
from services.historical.strategy_f5_dominant_nifty_regime_jul_sep_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIME_DEFINITION,
    SIDE_ALIGNMENT,
    WINDOW,
)


def test_full_development_protocol_reuses_frozen_regime_definition():
    assert PROTOCOL_VERSION == "STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_V1"
    assert WINDOW["start"] == "2026-07-01"
    assert WINDOW["end"] == "2026-09-30"
    assert REGIME_DEFINITION["magnitude_threshold"] is None
    assert REGIME_DEFINITION["source_protocol"] == (
        "STRATEGY_F5_DOMINANT_NIFTY_REGIME_V1"
    )
    assert SIDE_ALIGNMENT == {"BULLISH": "CE", "BEARISH": "PE"}
    assert GUARDRAILS["matched_baseline_trades_only"] is True
    assert GUARDRAILS["no_filtered_path_resimulation"] is True
    assert GUARDRAILS["do_not_use_as_same_day_entry_filter"] is True


def test_regime_status_mapping():
    assert _status("CE", "BULLISH") == "REGIME_ALIGNED"
    assert _status("PE", "BULLISH") == "COUNTER_REGIME"
    assert _status("PE", "BEARISH") == "REGIME_ALIGNED"
    assert _status("CE", "BEARISH") == "COUNTER_REGIME"
    assert _status("CE", "MIXED") == "MIXED_REGIME"


def test_summary_reports_wins_activations_and_net():
    rows = [
        {"net_pnl_inr": 100.0, "trail_activated": True},
        {"net_pnl_inr": -50.0, "trail_activated": False},
        {"net_pnl_inr": -25.0, "trail_activated": True},
    ]
    result = _summary(rows)
    assert result["trades"] == 3
    assert result["wins"] == 1
    assert result["losses"] == 2
    assert result["trail_activations"] == 2
    assert result["net_pnl_inr"] == 25.0
