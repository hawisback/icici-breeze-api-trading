"""Recorded combined-development short-swing strategy findings.

This module records development-only observations from the 152-session inspected
Cohort-1 + Cohort-2 corpus. It does not freeze a trading candidate, consume
blind data, or authorize implementation.
"""

SHORT_SWING_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_DEVELOPMENT_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_DEVELOPMENT_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "decision": "NO_CANDIDATE_FREEZE",
    "source": {
        "event_dataset_sha256": "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f",
        "cohort1_options_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "cohort2_options_sha256": "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc",
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "screen": {
        "non_overlapping_trades": True,
        "fixed_exit_minutes": [5, 10, 15, 30],
        "mean_reversion": {
            "range3_percentiles": [None, 50, 60, 70, 80],
            "absolute_close_location_thresholds": [0.4, 0.5, 0.6, 0.7, 0.8],
        },
        "continuation_and_failed_breakout": {
            "range3_percentiles": [None, 50, 70],
            "breakout_excursion_min_bps": [0, 1, 2, 4],
            "volume_vs_prior3_mean_min": [None, 1.0, 1.2],
        },
        "options_fast_lead_use": [
            "no filter",
            "directional agreement filter",
        ],
        "option_execution_variants": ["ATM", "one-strike ITM", "one-strike OTM"],
        "stop_target_optimization_performed": False,
    },
    "current_cost_assumptions": {
        "as_of": "2026-09-28",
        "nifty_lot_size": 65,
        "icici_brokerage_per_order_rupees": 20.0,
        "futures_stt_sell_rate": 0.0005,
        "options_stt_sell_premium_rate": 0.0015,
        "futures_exchange_transaction_rate_each_side": 0.0000183,
        "options_exchange_transaction_rate_each_side": 0.0003553,
        "sebi_turnover_rate_each_side": 0.000001,
        "futures_stamp_buy_rate": 0.00002,
        "options_stamp_buy_rate": 0.00003,
        "gst_rate": 0.18,
        "sources": [
            "https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax",
            "https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies",
            "https://www.icicidirect.com/brokerage",
            "https://www.icicidirect.com/faqs/fno/what-are-the-new-lot-sizes-for-index-derivatives",
        ],
    },
    "family_findings": {
        "short_mean_reversion": {
            "status": "REPEATED_BUT_TOO_SMALL_FOR_FUTURES_AND_OPTION_IMPLEMENTATIONS_TESTED",
            "representative_futures_rule": {
                "range_condition": None,
                "absolute_close_location_3_min": 0.8,
                "options_fast_lead_agrees": True,
                "exit_minutes": 10,
                "trades": 1292,
                "gross_mean_bps": 0.6814492975708978,
                "cohort1_mean_bps": 0.7990068463103857,
                "cohort2_mean_bps": 0.5532392848679611,
                "positive_block_means": "12/14",
            },
            "movement_conditioning_note": (
                "Adding a high recent-range condition did not materially improve "
                "the mean-reversion economics."
            ),
            "long_directional_option_note": (
                "The movement-conditioned ATM long-option implementation was negative "
                "after costs; the risk-defined 50-point credit-spread implementation "
                "was also negative across both cohorts."
            ),
        },
        "breakout_continuation": {
            "status": "NOT_CROSS_COHORT_STABLE",
            "note": (
                "Within the declared screen, continuation formulations were generally "
                "negative in Cohort 1 where Cohort 2 was positive; no stable formulation "
                "was promoted."
            ),
        },
        "failed_breakout_reversal": {
            "status": "DEVELOPMENT_LEAD_ONLY",
            "lead_rule_neighborhood": {
                "range_3_min_percentile": 70,
                "range_3_threshold_bps": 15.346337187399998,
                "failed_breakout_required": True,
                "volume_vs_prior3_mean_min": 1.2,
                "options_fast_lead_agrees": True,
                "exit_minutes": 30,
                "breakout_excursion_min_bps": "0-to-1 development neighborhood; not frozen",
            },
            "simple_rule_futures_gross": {
                "breakout_excursion_min_bps": 0,
                "trades": 126,
                "gross_mean_bps": 3.0230903545134917,
                "cohort1_mean_bps": 2.6640576549657897,
                "cohort2_mean_bps": 3.568820057826,
            },
            "futures_cost_screen": {
                "representative_current_roundtrip_cost_bps_before_slippage": 5.95293863340026,
                "conclusion": "CURRENT_FUTURES_COSTS_EXCEED_GROSS_EDGE",
            },
            "one_strike_itm_option_execution": {
                "breakout_excursion_min_bps": 0,
                "trades": 126,
                "sessions_traded": 74,
                "trades_per_session": 0.8289473684210527,
                "gross_mean_option_points": 4.135317460317461,
                "net_mean_points_no_slippage": 3.089570470491391,
                "net_mean_points_plus_1_point_each_side": 1.0895704704913909,
                "net_mean_points_plus_2_points_each_side": -0.9104295295086092,
                "cohort1_net_plus_1_point_each_side": 0.4946183634303646,
                "cohort2_net_plus_1_point_each_side": 1.993897673224152,
                "positive_block_means_plus_1_point_each_side": "6/14",
            },
            "more_selective_one_strike_itm_diagnostic": {
                "breakout_excursion_min_bps": 1,
                "trades": 111,
                "sessions_traded": 67,
                "gross_mean_option_points": 4.50135135135135,
                "net_mean_points_no_slippage": 3.4491204307803875,
                "net_mean_points_plus_1_point_each_side": 1.4491204307803882,
                "net_mean_points_plus_2_points_each_side": -0.5508795692196127,
                "cohort1_net_plus_1_point_each_side": 0.438658,
                "cohort2_net_plus_1_point_each_side": 3.174301,
                "positive_block_means_plus_1_point_each_side": "7/14",
                "session_cluster_bootstrap_mean_95pct_points": [
                    -2.69193177,
                    5.68677695,
                ],
                "cohort1_session_cluster_bootstrap_mean_95pct_points": [
                    -4.36073308,
                    5.18467983,
                ],
                "cohort2_session_cluster_bootstrap_mean_95pct_points": [
                    -4.25718473,
                    11.27915754,
                ],
                "worst_losing_streak": 5,
                "positive_session_rate": 0.5373134328358209,
                "temporal_note": (
                    "Later Cohort-1 months deteriorated; Jul/Aug/Sep 2026 were weak "
                    "or negative with small sample counts."
                ),
            },
        },
        "nondirectional_long_straddle": {
            "status": "REJECTED",
            "note": (
                "High recent 3-bar/6-bar range predicts subsequent movement magnitude, "
                "but buying the signal-time ATM straddle did not monetize it. The best "
                "screened high-range cases remained materially negative after current "
                "option costs and slippage."
            ),
        },
    },
    "interpretation": (
        "The combined development corpus contains repeatable microstructure and "
        "failed-breakout behavior, but no screened implementation is robust enough "
        "after conservative execution friction to justify freezing a candidate. "
        "Do not rescue these results with post-hoc time, DTE, stop, target, or strike "
        "filters and do not consume fresh blind data."
    ),
    "next_research_decision": (
        "Preserve failed-breakout reversal plus options agreement as a development "
        "lead only. Do not freeze or implement it. Move to a distinct, predeclared "
        "behavioral hypothesis rather than further tuning this lead."
    ),
}
