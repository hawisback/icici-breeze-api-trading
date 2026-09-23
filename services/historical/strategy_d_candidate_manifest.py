"""Frozen Strategy D V2 research candidate manifest.

V2 was selected after reviewing the Strategy D V1 Breeze backtest. The
fingerprint exists so the later paper monitor can refuse silent retuning.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from services.strategy.strategies.sr_momentum_breakout import (
    STRATEGY_D_V2_ID,
    StrategyDConfig,
)


CANDIDATE_ID = STRATEGY_D_V2_ID
FREEZE_DATE = date(2026, 9, 23)


def candidate_spec() -> dict[str, Any]:
    cfg = StrategyDConfig.v2_candidate()
    return {
        "candidate_id": CANDIDATE_ID,
        "freeze_date": FREEZE_DATE.isoformat(),
        "selected_after_v1_breeze_review": True,
        "entry": {
            "completed_spot_interval": "5m",
            "long_structure": ["PDH", "R1"],
            "short_structure": ["PDL", "S1"],
            "long_rsi_cross": cfg.long_rsi_cross,
            "short_rsi_cross": cfg.short_rsi_cross,
            "minimum_rsi_clearance_points_strict": (
                cfg.minimum_rsi_clearance_points
            ),
            "max_previous_day_range_atr_strict": (
                cfg.max_previous_day_range_atr
            ),
            "vwap_source": "ACTIVE_NIFTY_FUTURES_5M",
            "entry_start_ist": cfg.entry_start,
            "entry_end_ist": cfg.entry_end,
        },
        "risk_lifecycle_unchanged_from_v1": {
            "atr_period": cfg.atr_period,
            "atr_stop_multiple": cfg.atr_stop_multiple,
            "scale_out_r": cfg.scale_out_r,
            "scale_out_fraction": cfg.scale_out_fraction,
            "runner": "EMA9_5M_OR_R2_S2_OR_FORCE_EXIT",
            "force_exit_ist": cfg.force_exit,
        },
        "intrabar_ordering": {
            "preferred": "NATIVE_NIFTY_SPOT_1M",
            "same_minute_ambiguity": "PROTECTIVE_STOP_FIRST",
            "incomplete_1m": "EXPLICIT_CONSERVATIVE_5M_FALLBACK",
        },
        "not_enabled_as_hard_filters": {
            "r1_exclusion": False,
            "12_00_ist_blackout": False,
        },
        "production_thresholds_changed": False,
        "paper_or_live_enabled": False,
    }


def spec_fingerprint() -> str:
    payload = json.dumps(
        candidate_spec(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_v2_config(config: StrategyDConfig) -> None:
    expected = StrategyDConfig.v2_candidate()
    fields = (
        "variant",
        "rsi_period",
        "long_rsi_cross",
        "short_rsi_cross",
        "minimum_rsi_clearance_points",
        "atr_period",
        "max_previous_day_range_atr",
        "atr_stop_multiple",
        "scale_out_r",
        "scale_out_fraction",
        "entry_start",
        "entry_end",
        "force_exit",
    )
    mismatches = {
        field: {
            "expected": getattr(expected, field),
            "actual": getattr(config, field),
        }
        for field in fields
        if getattr(config, field) != getattr(expected, field)
    }
    if mismatches:
        raise ValueError(
            "Strategy D V2 candidate config drift: "
            + json.dumps(mismatches, sort_keys=True)
        )
