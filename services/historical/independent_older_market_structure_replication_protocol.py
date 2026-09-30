"""Frozen 2022-2024 historical replication of two preselected NIFTY range relationships."""
PROTOCOL_VERSION = "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_REPLICATION_V1"
CORPUS_ROLE = "OLDER_HISTORICAL_TEMPORAL_REPLICATION"

WINDOW = {
    "start": "2022-01-01",
    "end": "2024-12-31",
    "provider": "BREEZE",
    "interval": "5m",
    "session_start": "09:15",
    "session_last_bar": "15:25",
    "bars_per_session": 75,
}

CONTRACT_DISCOVERY = {
    "underlying": "NIFTY",
    "exchange": "NFO",
    "product_type": "futures",
    "nominal_expiry_weekday": "Thursday",
    "candidate_business_days_back": 5,
    "probe_calendar_days_back": 7,
    "minimum_probe_rows": 1,
    "manifest_must_be_complete_before_scoring": True,
}

RELATIONSHIPS = {
    "opening_range_vs_remaining_range": {
        "predictor": "first_30m_high_low_range_bps",
        "outcome": "post_09_40_remaining_session_high_low_range_bps",
    },
    "daily_range_persistence": {
        "predictor": "previous_session_high_low_range_bps",
        "outcome": "session_high_low_range_bps",
    },
}

REPLICATION_GATE = {
    "minimum_complete_sessions": 600,
    "pooled_spearman_must_be_positive": True,
    "calendar_year_spearman_must_be_positive_for_2022_2023_2024": True,
    "session_cluster_bootstrap_95pct_lower_must_be_positive": True,
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20260930,
    "cluster": "session",
    "interval": "percentile_95",
}

GUARDRAILS = {
    "research_only": True,
    "temporal_replication": True,
    "future_data_required": False,
    "feature_search": False,
    "posthoc_relationship_additions": False,
    "no_rescue_on_same_sample": True,
    "strategy_d_remains_paused": True,
}
