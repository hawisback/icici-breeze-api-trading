from datetime import datetime, timedelta

from services.historical.strategy_f5_conventional_regime_combinations import (
    _filtered_observations,
    _passes,
    _regime_lookup,
)
from services.historical.strategy_f5_conventional_regime_combinations_protocol import (
    CANDIDATE_PRIORITY,
    CANDIDATES,
    DEVELOPMENT_SCREEN,
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIME,
)


def _bars(n=80):
    start = datetime.fromisoformat("2026-07-01T09:15:00+05:30")
    bars = []
    close = 100.0
    for i in range(n):
        open_ = close
        move = 2.0 if i % 5 in (1, 2, 3) else -0.8
        close = max(10.0, close + move)
        bars.append({
            "timestamp": (start + timedelta(minutes=2 * i)).isoformat(),
            "date": "2026-07-01",
            "expiry": "2026-07-07",
            "strike": 25000,
            "right": "CE",
            "open": open_,
            "high": max(open_, close) + 1.0 + (i % 4) * 0.1,
            "low": min(open_, close) - 0.8,
            "close": close,
            "volume": 1000 + i * 10,
            "open_interest": 10000 + i * 50,
        })
    return bars


def test_protocol_has_small_fixed_candidate_set():
    assert PROTOCOL_VERSION == "STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_V1"
    assert CANDIDATE_PRIORITY == [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "ATR_BB_ACTIVE",
        "ATR_BB_STOCH80",
        "ATR_BB_HIST015",
        "ATR_BB_STOCH80_HIST015",
    ]
    assert REGIME["stochastic_not_overbought_max_exclusive"] == 80.0
    assert REGIME["histogram_strength_pct_min"] == 0.15
    assert DEVELOPMENT_SCREEN["selection"] == (
        "FIRST_PASSING_CANDIDATE_IN_PRIORITY_ORDER"
    )
    assert GUARDRAILS["no_numeric_threshold_search"] is True
    assert GUARDRAILS["keep_existing_post_activation_trail_frozen"] is True


def test_regime_lookup_uses_prior_medians_and_exposes_states():
    lookup = _regime_lookup(_bars())
    key = (
        "2026-07-01T11:55:00+05:30",
        "2026-07-07",
        25000,
        "CE",
    )
    assert key in lookup
    row = lookup[key]
    assert row["atr14_pct"] is not None
    assert row["atr14_prior20_median"] is not None
    assert row["bb_bandwidth20_pct"] is not None
    assert row["bb_prior20_median"] is not None
    assert isinstance(row["ATR_ACTIVE"], bool)
    assert isinstance(row["BB_ACTIVE"], bool)
    assert isinstance(row["STOCH_NOT_OVERBOUGHT"], bool)
    assert isinstance(row["HIST_STRONG"], bool)


def test_passes_requires_all_frozen_states():
    features = {
        "ATR_ACTIVE": True,
        "BB_ACTIVE": True,
        "STOCH_NOT_OVERBOUGHT": True,
        "HIST_STRONG": False,
    }
    assert _passes(features, "ATR_ACTIVE") is True
    assert _passes(features, "ATR_BB_STOCH80") is True
    assert _passes(features, "ATR_BB_HIST015") is False


def test_filter_only_changes_bullish_entry_flag_not_bearish_exit():
    observations = {
        "2026-07-01": {
            "2026-07-01T10:01:00+05:30": {
                "CE": {
                    "expiry": "2026-07-07",
                    "strike": 25000,
                    "right": "CE",
                    "bullish_cross": True,
                    "bearish_cross": False,
                }
            },
            "2026-07-01T10:03:00+05:30": {
                "CE": {
                    "expiry": "2026-07-07",
                    "strike": 25000,
                    "right": "CE",
                    "bullish_cross": False,
                    "bearish_cross": True,
                }
            },
        }
    }
    lookup = {
        (
            "2026-07-01T10:01:00+05:30",
            "2026-07-07",
            25000,
            "CE",
        ): {
            "ATR_ACTIVE": False,
            "BB_ACTIVE": True,
            "STOCH_NOT_OVERBOUGHT": True,
            "HIST_STRONG": True,
        }
    }

    filtered, missing = _filtered_observations(
        observations, lookup, "ATR_ACTIVE"
    )

    assert missing == 0
    assert filtered["2026-07-01"]["2026-07-01T10:01:00+05:30"]["CE"][
        "bullish_cross"
    ] is False
    assert filtered["2026-07-01"]["2026-07-01T10:03:00+05:30"]["CE"][
        "bearish_cross"
    ] is True
