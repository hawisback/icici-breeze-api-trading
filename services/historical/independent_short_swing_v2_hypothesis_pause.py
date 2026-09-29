"""Anti-mining pause after three distinct V2 development hypotheses.

The corrected 232-session V2 corpus has now been used for three newly frozen,
distinct hypotheses:
1. signed-volume / price-divergence catch-up,
2. session VWAP cross-through continuation,
3. prior-session late-day momentum spillover.

All three were rejected under their predeclared structural gates.

No fourth hypothesis may be generated ad hoc from the observed V2 outcomes.
Further development requires either:
- a hypothesis specified from an external rationale/source before inspecting
  any additional V2 outcome slice, or
- a newly frozen, non-blind development cohort collected under a predeclared
  protocol before the next endogenous hypothesis is formulated.

Blind validation remains untouched until a genuine candidate is frozen.
"""

PAUSE_VERSION = "SHORT_SWING_V2_HYPOTHESIS_MINING_PAUSE_V1"
V2_EVENT_SHA256 = (
    "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
)

POST_V2_STUDIES = [
    {
        "protocol": "SHORT_SWING_SIGNED_VOLUME_PRICE_DIVERGENCE_V1",
        "findings_sha256": (
            "652639cb6122ea6935eaab95376c38c462d7dc8b95fe26a874abf1444ec8ea47"
        ),
        "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    },
    {
        "protocol": "SHORT_SWING_SESSION_VWAP_CROSS_CONTINUATION_V1",
        "findings_sha256": (
            "b365858c45c361ed3e07ca35123476f43bcac211cc093ae197a8c1f873119b48"
        ),
        "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    },
    {
        "protocol": "SHORT_SWING_LATE_DAY_MOMENTUM_SPILLOVER_V1",
        "findings_sha256": (
            "0cd1253aafbefa57f4f086d77ddea6fefc7bb7af459af99d364c5edb1b381600"
        ),
        "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    },
]

NEXT_RESEARCH_GATE = {
    "ad_hoc_fourth_hypothesis_on_same_v2_outcomes_allowed": False,
    "allowed_path_external_hypothesis": (
        "Document a genuinely external economic/microstructure rationale and freeze "
        "the exact rule before any additional V2 outcome inspection."
    ),
    "allowed_path_new_development_data": (
        "Freeze dates/contracts/QA for a new inspected development cohort before "
        "formulating another endogenous hypothesis."
    ),
    "fresh_blind_data_allowed": False,
    "candidate_frozen": False,
    "implementation_allowed": False,
    "strategy_d_remains_paused": True,
}

GUARDRAILS = {
    "research_only": True,
    "blind_data_used": False,
    "no_retest_of_rejected_studies": True,
    "no_direction_flip_rescues": True,
    "no_threshold_rescues": True,
    "no_filter_rescues": True,
    "no_horizon_rescues": True,
    "no_ad_hoc_hypothesis_generation_from_observed_v2_results": True,
    "strategy_d_remains_paused": True,
}
