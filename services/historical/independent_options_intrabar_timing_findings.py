"""Recorded development-only intrabar timing result for the options lead observation.

This result does not freeze O3. It uses only the 80-session development corpus
and exists to determine when the previously observed 5-minute options-specific
lead is realized in NIFTY futures.
"""

DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1 = {
    "research_type": "NIFTY_OPTIONS_INTRABAR_TIMING_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "options_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "spot_sha256": "372703608cc10643e8f6fdb0ea4c0c91ba8676c19c4d3fe6c38b596304aab0cb",
        "intrabar_futures_sha256": "29fd1c41c57b40bb7813d83d2618114cd0eecc879ceb110cf36cd8a4658a28be",
        "sessions": 80,
        "one_minute_rows": 30000,
        "scorable_signals": 5840,
    },
    "intrabar_qa": {
        "complete_375_bar_sessions": "80/80",
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "wrong_contract_rows": 0,
        "failed_requests": 0,
        "five_minute_bars_reconciled": 6000,
        "one_minute_rows_per_five_minute_bar": 5,
        "exact_ohlcv_open_interest_reconciliation": True,
        "exact_contract_reconciliation": True,
        "timestamp_semantics": (
            "five-minute bar labelled t equals the aggregation of one-minute bars "
            "t through t+4; the signal is complete at t+5 and the t+5 one-minute "
            "open is the earliest bar-level execution proxy"
        ),
    },
    "signal": {
        "definition": (
            "the previously recorded leave-one-10-session-block-out residual of "
            "ATM options-implied relative move after spot gap and current futures "
            "return attribution"
        ),
        "definition_changed": False,
        "threshold_selected": False,
    },
    "boundary_from_signal_close_to_next_minute_open": {
        "spearman": 0.15867357045070798,
        "mean_direction_aligned_bps": 0.13962377500656642,
        "positive_correlation_blocks": "8/8",
        "positive_mean_blocks": "8/8",
    },
    "minute_open_to_close": {
        "minute_1": {
            "spearman": 0.1710651639326689,
            "mean_direction_aligned_bps": 0.4072887276291944,
            "median_direction_aligned_bps": 0.3682141406280426,
            "direction_hit_rate": 0.5493150684931507,
            "positive_correlation_blocks": "8/8",
            "positive_mean_blocks": "8/8",
            "session_cluster_bootstrap_mean_95pct_bps": [
                0.3344053911659273,
                0.4788839074469777,
            ],
        },
        "minute_2": {
            "spearman": 0.0047942860918651214,
            "mean_direction_aligned_bps": 0.0205942196076119,
            "positive_correlation_blocks": "4/8",
            "positive_mean_blocks": "4/8",
        },
        "minute_3": {
            "spearman": -0.0009232602448277477,
            "mean_direction_aligned_bps": -0.005846460379400694,
            "positive_correlation_blocks": "3/8",
            "positive_mean_blocks": "4/8",
        },
        "minute_4": {
            "spearman": -0.003664335837112343,
            "mean_direction_aligned_bps": -0.012344805667767786,
            "positive_correlation_blocks": "4/8",
            "positive_mean_blocks": "3/8",
        },
        "minute_5": {
            "spearman": -0.004373599957293132,
            "mean_direction_aligned_bps": 0.025657183535155205,
            "positive_correlation_blocks": "5/8",
            "positive_mean_blocks": "5/8",
        },
    },
    "cumulative_from_earliest_next_minute_open": {
        "minute_1_mean_aligned_bps": 0.4072887276291944,
        "minute_2_mean_aligned_bps": 0.4278859024793019,
        "minute_3_mean_aligned_bps": 0.4220247436669919,
        "minute_4_mean_aligned_bps": 0.4097113713361048,
        "minute_5_mean_aligned_bps": 0.43536103585608393,
        "minute_5_spearman": 0.09785121668836147,
        "minute_5_positive_correlation_blocks": "8/8",
        "minute_5_session_cluster_bootstrap_mean_95pct_bps": [
            0.2905050346325914,
            0.5825833798026065,
        ],
    },
    "latency_challenge": {
        "entry_minute_1_open_to_minute_5_close": {
            "spearman": 0.09785121668836147,
            "mean_direction_aligned_bps": 0.43536103585608393,
            "positive_correlation_blocks": "8/8",
        },
        "entry_minute_2_open_to_minute_5_close": {
            "spearman": 0.006557825714201001,
            "mean_direction_aligned_bps": 0.025738000906009903,
            "positive_correlation_blocks": "3/8",
        },
        "interpretation": (
            "A one-minute delayed entry removes essentially all of the remaining "
            "development relationship."
        ),
    },
    "magnitude_challenge": {
        "warning": "descriptive only; no magnitude threshold is frozen",
        "absolute_signal_quartile_minute_1_mean_aligned_bps": [
            0.14566499472999214,
            0.21490327754382957,
            0.4167574116625622,
            0.851829226580394,
        ],
        "interpretation": (
            "The upper quartile is stronger but remains below one basis point gross "
            "on average; quartiles are not promoted into a candidate filter."
        ),
    },
    "economic_screen": {
        "average_futures_price": 24068.128219178085,
        "minute_1_mean_aligned_points": 0.980267731880535,
        "upper_quartile_minute_1_mean_aligned_points": 2.0446842349161987,
        "futures_stt_rate_from_2026_04_01_bps_on_sell_value": 5.0,
        "note": (
            "STT alone exceeds the observed gross futures edge before brokerage, "
            "exchange charges, stamp duty, GST and slippage. The tax rate is an "
            "external current-market fact and is not used to fit the signal."
        ),
    },
    "status": "FAST_PRICE_DISCOVERY_BEHAVIOR_NOT_FROZEN_AS_O3",
    "interpretation": (
        "The development evidence supports a fast options-specific price-discovery "
        "relationship, but almost all executable-proxy movement is concentrated in "
        "the first minute after the five-minute signal closes. The relationship is "
        "essentially absent after a one-minute delay and its gross futures magnitude "
        "is too small to justify spending fresh blind data as a futures trading candidate."
    ),
    "next_research_decision": (
        "Preserve this as descriptive microstructure evidence. Do not freeze O3, "
        "do not select a magnitude threshold, and do not consume Blind11 or another "
        "fresh blind block for this formulation. Return to development data for a "
        "distinct options behavior with a longer-lived and economically meaningful horizon."
    ),
}
