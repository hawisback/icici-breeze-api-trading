"""Frozen strategy-independent discovery hypotheses.

Discovery block:
    2026-09-10 through 2026-09-25, ten complete NIFTY sessions.

These rules were formulated from the enriched independent-market dataset and are
frozen *before* any older block is inspected. Discovery metrics below are
hypothesis-generation evidence only, not out-of-sample validation and not options
PnL.

Common feature semantics
------------------------
- Research instrument: selected NIFTY futures 5-minute candles.
- Signal features use only information available at the completed signal bar.
- TR = max(high-low, abs(high-prev_close), abs(low-prev_close)).
- ATR14 = within-session rolling mean of TR over 14 bars, min_periods=8.
- MOM3 = futures_close[t] - futures_close[t-3].
- OI3 = futures_open_interest[t] - futures_open_interest[t-3].
- VWAP = cumulative session typical-price ((H+L+C)/3) * volume / cumulative volume.
- OR15 = high/low of the first three 5-minute bars.
- Signal bars are 09:30 through 14:45, but ATR availability makes 09:50 the
  earliest executable signal in this discovery implementation.
- Episode de-duplication: signal only when the condition transitions false->true,
  and require at least 6 bars since the prior entry for the same hypothesis.
- Entry = next 5-minute bar open.
- Stop = 1.0 * signal-bar ATR14.
- Target = 1.0 * signal-bar ATR14.
- Maximum hold = 6 bars (30 minutes); otherwise exit at the sixth bar close.
- No same-bar stop/target ambiguity occurred in the discovery block for the
  candidates below.

The manifest intentionally contains no tuning ranges.
"""

from __future__ import annotations

DISCOVERY_BLOCK = {
    "id": "IR-DISCOVERY-01",
    "session_dates": [
        "2026-09-10",
        "2026-09-11",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
    ],
    "interval_minutes": 5,
    "entry_start": "09:30",
    "entry_end": "14:45",
    "atr_window": 14,
    "atr_min_periods": 8,
    "episode_min_gap_bars": 6,
    "entry": "NEXT_BAR_OPEN",
    "target_atr": 1.0,
    "stop_atr": 1.0,
    "max_hold_bars": 6,
}

CANDIDATES = {
    "IR-H1-DOWN-OI-CONTINUATION": {
        "status": "FROZEN_FOR_BLIND_VALIDATION",
        "side": "SHORT",
        "conditions": [
            "MOM3 < 0",
            "OI3 < 0",
        ],
        "interpretation": (
            "Downside momentum accompanied by falling open interest; test whether "
            "the long-unwinding state tends to continue lower over the next 30 minutes."
        ),
        "discovery": {
            "episodes": 42,
            "active_days": 10,
            "simulated_total_r": 10.536,
            "simulated_mean_r": 0.251,
            "simulated_win_rate": 0.643,
            "simulated_max_drawdown_r": -3.0,
            "mean_directional_30m_atr": 0.343,
            "median_directional_30m_atr": 0.380,
            "mean_directional_60m_atr": 0.277,
            "day_cluster_bootstrap_mean_r_95pct": [0.042, 0.448],
        },
    },
    "IR-H2-DOWN-OI-ABOVE-VWAP": {
        "status": "FROZEN_FOR_BLIND_VALIDATION",
        "side": "SHORT",
        "conditions": [
            "MOM3 < 0",
            "OI3 < 0",
            "FUTURES_CLOSE > SESSION_VWAP",
        ],
        "interpretation": (
            "A selective long-unwinding pullback while futures are still above "
            "session VWAP; tests continued reversion/pressure toward and through VWAP."
        ),
        "discovery": {
            "episodes": 21,
            "active_days": 8,
            "simulated_total_r": 8.564,
            "simulated_mean_r": 0.408,
            "simulated_win_rate": 0.762,
            "simulated_max_drawdown_r": -2.0,
            "mean_directional_30m_atr": 0.531,
            "median_directional_30m_atr": 0.751,
            "mean_directional_60m_atr": 0.573,
            "day_cluster_bootstrap_mean_r_95pct": [0.118, 0.785],
        },
    },
    "IR-H3-UP-IMPULSE-REVERSAL": {
        "status": "FROZEN_FOR_BLIND_VALIDATION",
        "side": "SHORT",
        "conditions": [
            "MOM3 / ATR14 >= 0.5",
        ],
        "interpretation": (
            "Short-horizon mean reversion after a strong three-bar upside impulse. "
            "The discovery edge decayed by 60 minutes, so the 30-minute maximum "
            "hold is part of the frozen hypothesis."
        ),
        "discovery": {
            "episodes": 54,
            "active_days": 10,
            "simulated_total_r": 11.960,
            "simulated_mean_r": 0.221,
            "simulated_win_rate": 0.648,
            "simulated_max_drawdown_r": -5.044,
            "mean_directional_30m_atr": 0.197,
            "median_directional_30m_atr": 0.326,
            "mean_directional_60m_atr": -0.178,
            "day_cluster_bootstrap_mean_r_95pct": [-0.081, 0.516],
        },
    },
    "IR-H4-OR15-BREAKDOWN-CONTINUATION": {
        "status": "FROZEN_SECONDARY_FOR_BLIND_VALIDATION",
        "side": "SHORT",
        "conditions": [
            "MOM3 < 0",
            "FUTURES_CLOSE < OR15_LOW",
        ],
        "interpretation": (
            "Downside continuation while price is below the first-15-minute range. "
            "Discovery performance was strong but occurred on only six active days, "
            "so this remains a secondary candidate."
        ),
        "discovery": {
            "episodes": 23,
            "active_days": 6,
            "simulated_total_r": 9.491,
            "simulated_mean_r": 0.413,
            "simulated_win_rate": 0.696,
            "simulated_max_drawdown_r": -2.0,
            "mean_directional_30m_atr": 0.777,
            "median_directional_30m_atr": 0.804,
            "mean_directional_60m_atr": 0.670,
            "day_cluster_bootstrap_mean_r_95pct": [-0.108, 0.724],
        },
    },
}

REGIME_HYPOTHESES = {
    "IR-R1-VIX-EXPANSION": {
        "status": "FROZEN_FOR_BLIND_VALIDATION",
        "condition": "VIX_CLOSE[t] - VIX_CLOSE[t-6] >= 0.06",
        "outcome": "NEXT_30M_MAX_FUTURES_EXCURSION_POINTS",
        "discovery": {
            "top_quartile_threshold": 0.06,
            "top_quartile_mean_future_excursion_points": 43.14,
            "all_bar_mean_future_excursion_points": 37.95,
            "bottom_quartile_threshold": -0.07,
            "bottom_quartile_mean_future_excursion_points": 37.39,
        },
        "interpretation": (
            "Use as a volatility/target-sizing regime candidate, not as a standalone "
            "directional entry."
        ),
    },
}
