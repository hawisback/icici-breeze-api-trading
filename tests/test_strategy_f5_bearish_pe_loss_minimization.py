from services.historical.strategy_f5_bearish_pe_loss_minimization import (
    _breadth_status,
    _candidate_condition,
    _checkpoint_state,
)
from services.historical.strategy_f5_bearish_pe_loss_minimization_protocol import (
    CANDIDATES,
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def test_protocol_targets_only_bearish_pe_loss_control():
    assert PROTOCOL_VERSION == "STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_V1"
    assert GUARDRAILS["target_only_bearish_regime_PE"] is True
    assert GUARDRAILS["breadth_confirmed_is_secondary_slice_not_required_gate"] is True
    assert GUARDRAILS["no_post_activation_change"] is True
    assert GUARDRAILS["any_selected_candidate_requires_fresh_holdout"] is True


def test_frozen_candidate_family_is_small_and_fixed():
    assert list(CANDIDATES) == [
        "NO_POSITIVE_CLOSE_BY_6M",
        "NO_POSITIVE_CLOSE_BY_10M",
        "MFE_LT_2PCT_BY_10M",
    ]


def test_breadth_status_keeps_unavailable_separate():
    assert _breadth_status(
        "BEARISH",
        {"available": False, "breadth_direction": None},
    ) == "UNAVAILABLE"
    assert _breadth_status(
        "BEARISH",
        {"available": True, "breadth_direction": "BEARISH"},
    ) == "CONFIRMED"
    assert _breadth_status(
        "BEARISH",
        {"available": True, "breadth_direction": "BULLISH"},
    ) == "AVAILABLE_NOT_CONFIRMED"


def test_no_positive_close_candidates_use_mfe_at_or_below_zero():
    state = {
        "checkpoint_available": True,
        "candidate_eligible": True,
        "max_favorable_close_return_pct": 0.0,
    }
    assert _candidate_condition("NO_POSITIVE_CLOSE_BY_6M", state) is True
    assert _candidate_condition("NO_POSITIVE_CLOSE_BY_10M", state) is True


def test_two_percent_mfe_candidate_is_strictly_below_two():
    low = {
        "checkpoint_available": True,
        "candidate_eligible": True,
        "max_favorable_close_return_pct": 1.99,
    }
    exact = {
        "checkpoint_available": True,
        "candidate_eligible": True,
        "max_favorable_close_return_pct": 2.0,
    }
    assert _candidate_condition("MFE_LT_2PCT_BY_10M", low) is True
    assert _candidate_condition("MFE_LT_2PCT_BY_10M", exact) is False


def test_missing_checkpoint_never_triggers():
    state = {
        "checkpoint_available": False,
        "candidate_eligible": False,
        "max_favorable_close_return_pct": None,
    }
    assert _candidate_condition("NO_POSITIVE_CLOSE_BY_6M", state) is False


def test_ineligible_checkpoint_never_triggers_even_with_no_progress():
    state = {
        "checkpoint_available": True,
        "candidate_eligible": False,
        "max_favorable_close_return_pct": -5.0,
    }
    assert _candidate_condition("NO_POSITIVE_CLOSE_BY_6M", state) is False


def test_checkpoint_state_defines_checkpoint_from_entry_timestamp():
    trade = {
        "date": "2026-07-01",
        "entry_timestamp": "2026-07-01T09:30:00+05:30",
        "exit_timestamp": "2026-07-01T09:40:00+05:30",
        "trail_activation_timestamp": None,
        "right": "PE",
        "entry_open": 100.0,
    }
    obs_by_day = {
        "2026-07-01": {
            "2026-07-01T09:32:00+05:30": {
                "PE": {"close": 99.0, "macd_hist_pct": 0.1, "rvi": 55.0}
            },
            "2026-07-01T09:34:00+05:30": {
                "PE": {"close": 98.0, "macd_hist_pct": 0.08, "rvi": 52.0}
            },
            "2026-07-01T09:36:00+05:30": {
                "PE": {"close": 97.0, "macd_hist_pct": 0.05, "rvi": 49.0}
            },
        }
    }
    state = _checkpoint_state(trade, obs_by_day, 6)
    assert state["checkpoint_available"] is True
    assert state["candidate_eligible"] is True
    assert state["observations"] == 3
