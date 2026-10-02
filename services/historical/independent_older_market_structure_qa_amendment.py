"""QA amendment for the older Breeze market-structure replication.

Frozen after expiry-manifest discovery and before any market-pattern scoring.

The original protocol requested 2022-01-01 through 2024-12-31. Breeze returned
zero rows for every frozen January-2022 monthly-expiry candidate, while
February-2022 onward resolved normally. January 2022 is therefore excluded for
provider data availability only. No relationship, statistic, gate, or scoring
rule is changed.
"""

AMENDMENT_VERSION = "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1"

OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1 = {
    "amendment_version": AMENDMENT_VERSION,
    "protocol_version": "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_REPLICATION_V1",
    "reason": "BREEZE_PROVIDER_DATA_UNAVAILABLE_FOR_2022_01",
    "expiry_manifest_sha256": (
        "550be778e784d7a9e6c21808cd5b633b67e7bb61981fac750b5799cc53adb0f3"
    ),
    "manifest_provider": "BREEZE",
    "manifest_months_expected": 37,
    "manifest_months_resolved": 36,
    "unresolved_months": ["2022-01"],
    "january_2022_probe_candidates": [
        "2022-01-27",
        "2022-01-26",
        "2022-01-25",
        "2022-01-24",
        "2022-01-21",
    ],
    "january_2022_probe_rows": [0, 0, 0, 0, 0],
    "original_window_start": "2022-01-01",
    "effective_window_start": "2022-02-01",
    "window_end_unchanged": "2024-12-31",
    "required_resolved_contract_months": [
        "2022-02",
        "2022-03",
        "2022-04",
        "2022-05",
        "2022-06",
        "2022-07",
        "2022-08",
        "2022-09",
        "2022-10",
        "2022-11",
        "2022-12",
        "2023-01",
        "2023-02",
        "2023-03",
        "2023-04",
        "2023-05",
        "2023-06",
        "2023-07",
        "2023-08",
        "2023-09",
        "2023-10",
        "2023-11",
        "2023-12",
        "2024-01",
        "2024-02",
        "2024-03",
        "2024-04",
        "2024-05",
        "2024-06",
        "2024-07",
        "2024-08",
        "2024-09",
        "2024-10",
        "2024-11",
        "2024-12",
        "2025-01",
    ],
    "relationships_changed": False,
    "replication_gate_changed": False,
    "bootstrap_changed": False,
    "outcomes_inspected_before_amendment": False,
    "pattern_scoring_performed_before_amendment": False,
    "no_rescue_or_tuning": True,
}
