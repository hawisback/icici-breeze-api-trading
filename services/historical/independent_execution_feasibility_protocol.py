"""Frozen execution-feasibility audit for the retained options fast-lead timing effect.

This is a NEW research track after the directional short-swing project was
closed with NO_CANDIDATE_FREEZE. It does not reopen any rejected trading rule.

Question
--------
Can the previously recorded earliest-executable-minute futures effect clear the
unavoidable current futures cost floor before spread/slippage modelling?

This audit is intentionally one-way and non-tunable. If the recorded gross edge
is already below unavoidable statutory costs, no L1 spread/slippage collection
is justified for this formulation.

Frozen evidence
---------------
Source A: independent_options_intrabar_timing_findings.py
Git blob SHA: e10d9f773d073d9b3e361dc6ee4db9593ec6f334
- minute-1 mean direction-aligned gross return: 0.4072887276291944 bps
- descriptive upper-quartile minute-1 mean: 0.851829226580394 bps
- one-minute delayed entry to minute-5 close mean: 0.025738000906009903 bps

Source B: independent_short_swing_development_findings.py
Git blob SHA: 0297ab0b50ec65eeb1a4b3d58945c3a49ccfc62d
- representative current futures roundtrip explicit cost before slippage:
  5.95293863340026 bps
- cost assumptions dated 2026-09-28.

External current-cost verification frozen on 2026-09-30:
- NSE current STT table: sale of securities futures = 0.05% of sell value
  (= 5.0 bps on the sell leg).
- NSE circular NSE/FA/73061: equity-futures exchange outflow = Rs 183 per
  crore each side, effective 2026-03-01.
- ICICI Direct iValue page: futures brokerage = Rs 20 per order.

No current-market external fact is used to fit or select the signal.
"""

PROTOCOL_VERSION = "NIFTY_EXECUTION_FEASIBILITY_AUDIT_V1"

FROZEN_INPUTS = {
    "timing_findings_blob_sha": "e10d9f773d073d9b3e361dc6ee4db9593ec6f334",
    "development_findings_blob_sha": "0297ab0b50ec65eeb1a4b3d58945c3a49ccfc62d",
}

FROZEN_GROSS_EVIDENCE_BPS = {
    "minute1_mean": 0.4072887276291944,
    "minute1_upper_quartile_mean_descriptive_only": 0.851829226580394,
    "one_minute_delayed_entry_to_minute5_close_mean": 0.025738000906009903,
}

FROZEN_COST_FLOORS_BPS = {
    "current_futures_stt_sell_leg_only": 5.0,
    "representative_roundtrip_explicit_cost_before_slippage": 5.95293863340026,
}

DECISION_RULE = {
    "primary": (
        "If minute1_mean <= current_futures_stt_sell_leg_only, classify the "
        "formulation as economically infeasible for futures execution and do "
        "not collect L1 spread/slippage data for it."
    ),
    "secondary": (
        "Compare the descriptive upper-quartile minute1 mean to the same STT "
        "floor only as a robustness diagnostic; it cannot create a candidate."
    ),
    "latency": (
        "Record the one-minute-delayed remaining gross effect, but do not alter "
        "entry timing or rescue the formulation."
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "does_not_reopen_directional_research": True,
    "no_signal_threshold_selection": True,
    "no_slippage_tuning": True,
    "no_spread_model_tuning": True,
    "no_new_broker_or_trading_api": True,
    "breeze_collection_required": False,
    "l1_collection_allowed_only_if_cost_floor_passes": True,
    "strategy_d_remains_paused": True,
}
