import numpy as np
import pandas as pd

from services.historical.independent_short_swing_development_v2_protocol import (
    EXPECTED,
    FEATURE_SEMANTICS_V2,
    FROZEN_INPUTS,
    GUARDRAILS,
    PROTOCOL_VERSION,
)
from services.historical.independent_short_swing_event_dataset_v2 import (
    _event_columns,
    _recompute_state_features,
)


def _feature_frame():
    rows = []
    for block, day in ((1, "2026-01-02"), (2, "2026-01-05")):
        base = pd.Timestamp(f"{day}T09:15:00+05:30")
        for index in range(7):
            close = 100.0 + block + index
            rows.append({
                "cohort": "cohortX",
                "cohort_block": block,
                "timestamp": base + pd.Timedelta(minutes=5 * index),
                "date": day,
                "futures_open": close - 0.5,
                "futures_high": close + 1.0,
                "futures_low": close - 1.0,
                "futures_close": close,
                "futures_volume": 1000.0 + 100.0 * index,
                "futures_open_interest": 500000.0 + 10.0 * index,
                "vix_close": 15.0 + index / 10.0,
                "spot_close": close - 5.0,
                "options_gap_bps": float(index + block),
            })
    return pd.DataFrame(rows)


def test_v2_protocol_freezes_inputs_and_expected_shape():
    assert PROTOCOL_VERSION == "SHORT_SWING_DEVELOPMENT_V2"
    assert FROZEN_INPUTS["cohort1_cohort2_event_v1"]["sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert FROZEN_INPUTS["cohort3_source_manifest"]["sha256"] == (
        "1d622797b0a97fba72da68bc78ba67d0b09beac525c1a074525764332591c166"
    )
    assert EXPECTED["sessions"] == 232
    assert EXPECTED["five_minute_events"] == 17400
    assert EXPECTED["chronological_blocks"] == 22
    assert EXPECTED["scorable_events_by_horizon"] == {
        "5": 17168,
        "10": 16936,
        "15": 16704,
        "30": 16008,
    }


def test_v2_full_window_semantics_forbid_partial_early_session_features():
    result = _recompute_state_features(_feature_frame())

    for day in ("2026-01-02", "2026-01-05"):
        day_rows = result.loc[result["date"] == day].reset_index(drop=True)

        assert pd.isna(day_rows.loc[0, "futures_return_bps"])
        assert day_rows.loc[1:, "futures_return_bps"].notna().all()

        assert day_rows.loc[:1, "range_3_bps"].isna().all()
        assert day_rows.loc[2:, "range_3_bps"].notna().all()

        assert day_rows.loc[:4, "range_6_bps"].isna().all()
        assert day_rows.loc[5:, "range_6_bps"].notna().all()

        assert day_rows.loc[:2, "prior_3_high"].isna().all()
        assert day_rows.loc[:2, "prior_3_low"].isna().all()
        assert day_rows.loc[:2, "volume_vs_prior3_mean"].isna().all()
        assert day_rows.loc[3:, "prior_3_high"].notna().all()
        assert day_rows.loc[3:, "prior_3_low"].notna().all()
        assert day_rows.loc[3:, "volume_vs_prior3_mean"].notna().all()

        assert day_rows.loc[:2, "path_length_3_bps"].isna().all()
        assert day_rows.loc[3:, "path_length_3_bps"].notna().all()

        assert day_rows.loc[:5, "path_length_6_bps"].isna().all()
        assert pd.notna(day_rows.loc[6, "path_length_6_bps"])


def test_prior3_volume_mean_uses_exact_three_previous_bars():
    result = _recompute_state_features(_feature_frame())
    day = result.loc[result["date"] == "2026-01-02"].reset_index(drop=True)

    expected = np.mean([1000.0, 1100.0, 1200.0])
    actual_ratio = 1300.0 / expected
    assert day.loc[3, "volume_vs_prior3_mean"] == actual_ratio


def test_v2_features_do_not_modify_unrelated_columns():
    frame = _feature_frame()
    frame["entry_price"] = np.arange(len(frame), dtype=float) + 200.0
    frame["h5m_terminal_bps"] = np.arange(len(frame), dtype=float)
    result = _recompute_state_features(frame)

    assert result["entry_price"].tolist() == frame["entry_price"].tolist()
    assert result["h5m_terminal_bps"].tolist() == frame["h5m_terminal_bps"].tolist()


def test_v2_builder_guardrails_do_not_promote_or_use_blind_data():
    assert FEATURE_SEMANTICS_V2["full_declared_window_required"] is True
    assert FEATURE_SEMANTICS_V2["partial_window_statistics_forbidden"] is True
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["candidate_frozen"] is False
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["strategy_scoring_in_builder"] is False
    assert GUARDRAILS["threshold_search_in_builder"] is False
    assert GUARDRAILS["no_retroactive_rescue_of_prior_hypotheses"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_v2_event_schema_keeps_fixed_horizon_execution_fields():
    columns = _event_columns()
    assert "entry_timestamp" in columns
    assert "entry_price" in columns
    for horizon in (5, 10, 15, 30):
        assert f"h{horizon}m_terminal_bps" in columns
        assert f"h{horizon}m_long_mfe_bps" in columns
        assert f"h{horizon}m_short_mae_bps" in columns
