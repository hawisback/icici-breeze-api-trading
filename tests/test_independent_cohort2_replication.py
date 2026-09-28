from services.historical.independent_cohort2_replication import (
    COHORT1_BLOCK_SIZE,
    COHORT2_BLOCK_SIZE,
    _replication_check,
)
from services.historical.independent_cohort2_protocol import PROTOCOL_VERSION


def test_replication_block_sizes_are_frozen_before_collection():
    assert COHORT1_BLOCK_SIZE == 10
    assert COHORT2_BLOCK_SIZE == 12
    assert PROTOCOL_VERSION == "DEVELOPMENT_COHORT_2_V1"


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
