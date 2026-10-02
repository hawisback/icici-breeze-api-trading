import pandas as pd

from services.historical.independent_cohort2_replication import (
    COHORT1_BLOCK_SIZE,
    COHORT2_BLOCK_SIZE,
    _replication_check,
    _spearman,
    build_frame,
)
from services.historical.independent_cohort2_protocol import PROTOCOL_VERSION


def test_replication_block_sizes_are_frozen_before_collection():
    assert COHORT1_BLOCK_SIZE == 10
    assert COHORT2_BLOCK_SIZE == 12
    assert PROTOCOL_VERSION == "DEVELOPMENT_COHORT_2_V1_1"


def test_replication_requires_same_sign_and_five_of_six_blocks():
    cohort1 = {"metric": {"spearman": 0.4}}
    cohort2 = {
        "metric": {
            "spearman": 0.2,
            "positive_blocks": 5,
            "negative_blocks": 1,
            "total_blocks": 6,
        }
    }
    result = _replication_check(cohort1, cohort2, ["metric"])
    assert result["same_pooled_sign"] is True
    assert result["cohort2_same_sign_blocks"] == 5
    assert result["directional_replication"] is True


def test_replication_does_not_rescue_wrong_pooled_sign():
    cohort1 = {"metric": {"spearman": -0.1}}
    cohort2 = {
        "metric": {
            "spearman": 0.05,
            "positive_blocks": 5,
            "negative_blocks": 1,
            "total_blocks": 6,
        }
    }
    result = _replication_check(cohort1, cohort2, ["metric"])
    assert result["same_pooled_sign"] is False
    assert result["directional_replication"] is False


def test_replication_does_not_promote_four_of_six_blocks():
    cohort1 = {"metric": {"spearman": 0.4}}
    cohort2 = {
        "metric": {
            "spearman": 0.3,
            "positive_blocks": 4,
            "negative_blocks": 2,
            "total_blocks": 6,
        }
    }
    result = _replication_check(cohort1, cohort2, ["metric"])
    assert result["same_pooled_sign"] is True
    assert result["directional_replication"] is False


def test_build_frame_replaces_null_market_auxiliary_placeholders():
    sessions = ["2026-01-02"]
    market_rows = []
    vix_rows = []
    spot_rows = []
    option_rows = []
    for index in range(6):
        minute = 15 + index * 5
        timestamp = f"2026-01-02T09:{minute:02d}:00+05:30"
        price = 26000.0 + index
        market_rows.append({
            "timestamp": timestamp,
            "futures_open": price,
            "futures_high": price + 2.0,
            "futures_low": price - 2.0,
            "futures_close": price + 1.0,
            "futures_volume": 1000.0 + index,
            "futures_open_interest": 10000.0 + index,
            "futures_instrument": "NIFTY FUT 2026-01-27",
            "vix_close": None,
            "spot_close": None,
        })
        vix_rows.append({"timestamp": timestamp, "close": 12.0 + index / 10.0})
        spot_rows.append({"timestamp": timestamp, "close": 25950.0 + index})
        for right, close in (("CE", 100.0 + index), ("PE", 99.0 + index)):
            option_rows.append({
                "timestamp": timestamp,
                "expiry": "2026-01-06",
                "strike": 26000,
                "right": right,
                "close": close,
            })
    frame = build_frame(
        {"session_dates": sessions, "canonical_market_rows": market_rows},
        {"session_dates": sessions, "vix_rows": vix_rows},
        {
            "session_dates": sessions,
            "contract_by_date": {"2026-01-02": "2026-01-06"},
            "option_candles": option_rows,
        },
        {"session_dates": sessions, "spot_rows": spot_rows},
        block_size=1,
    )
    assert "vix_close" in frame.columns
    assert "spot_close" in frame.columns
    assert "vix_close_x" not in frame.columns
    assert "vix_close_y" not in frame.columns
    assert "spot_close_x" not in frame.columns
    assert "spot_close_y" not in frame.columns
    assert frame["vix_close"].tolist() == [12.0 + index / 10.0 for index in range(6)]
    assert frame["spot_close"].tolist() == [25950.0 + index for index in range(6)]


def test_spearman_dependency_executes():
    left = pd.Series([1.0, 2.0, 3.0, 4.0])
    right = pd.Series([4.0, 3.0, 2.0, 1.0])
    assert _spearman(left, right) == -1.0
