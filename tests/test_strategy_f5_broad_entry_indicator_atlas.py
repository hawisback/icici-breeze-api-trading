from datetime import datetime, timedelta

import numpy as np
import pytest

from services.historical.strategy_f5_broad_entry_indicator_atlas import (
    _feature_lookup,
    _rank_auc,
)
from services.historical.strategy_f5_broad_entry_indicator_atlas_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def _bars(n=80):
    start = datetime.fromisoformat("2026-07-01T09:15:00+05:30")
    bars = []
    close = 100.0
    for i in range(n):
        open_ = close
        move = 1.8 if i % 5 in (1, 2, 3) else -0.9
        close = max(10.0, close + move)
        high = max(open_, close) + 0.8 + (i % 3) * 0.1
        low = min(open_, close) - 0.7
        bars.append({
            "timestamp": (start + timedelta(minutes=2 * i)).isoformat(),
            "date": "2026-07-01",
            "expiry": "2026-07-07",
            "strike": 25000,
            "right": "CE",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 100 + (i % 7) * 20 + i,
            "open_interest": 1000 + i,
        })
    return bars


def test_atlas_protocol_is_broad_discovery_only():
    assert PROTOCOL_VERSION == "STRATEGY_F5_BROAD_ENTRY_INDICATOR_ATLAS_V1"
    expected = {
        "rsi14",
        "stoch_k14",
        "williams_r14",
        "cci20",
        "roc5_pct",
        "adx14",
        "atr14_pct",
        "bb_percent_b20",
        "realized_vol_ratio_5_20",
        "mfi14",
        "cmf20",
        "vwap_distance_pct",
        "days_to_expiry",
    }
    assert expected.issubset(set(CONTINUOUS_FEATURES))
    assert GUARDRAILS["no_numeric_cutpoint_search"] is True
    assert GUARDRAILS["no_feature_combination_search"] is True
    assert GUARDRAILS["live_execution"] is False


def test_feature_lookup_builds_all_indicators_from_completed_2m_bars():
    bars = _bars()
    lookup = _feature_lookup(bars)
    key = (
        "2026-07-01T11:55:00+05:30",
        "2026-07-07",
        25000,
        "CE",
    )
    assert key in lookup
    row = lookup[key]

    for feature in CONTINUOUS_FEATURES:
        if feature == "entry_premium":
            assert row[feature] is None
        else:
            assert feature in row

    assert 0.0 <= row["rsi14"] <= 100.0
    assert 0.0 <= row["stoch_k14"] <= 100.0
    assert -100.0 <= row["williams_r14"] <= 0.0
    assert 0.0 <= row["mfi14"] <= 100.0
    assert row["days_to_expiry"] == 6.0
    assert row["minutes_from_open"] == pytest.approx(158.0)


def test_rank_auc_direction_is_correct():
    pos = np.asarray([5.0, 6.0, 7.0])
    neg = np.asarray([1.0, 2.0, 3.0])
    assert _rank_auc(pos, neg) == pytest.approx(1.0)
    assert _rank_auc(neg, pos) == pytest.approx(0.0)
