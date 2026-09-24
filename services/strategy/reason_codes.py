"""Canonical Strategy A lifecycle reason codes."""

from __future__ import annotations


OPTION_EMERGENCY_STOP = "OPTION_EMERGENCY_STOP"
OPTION_HARD_STOP_HIT_COMPAT_ALIAS = "OPTION_HARD_STOP_HIT"
OPTION_EMERGENCY_STOP_UNDERLYING_REASON = "OPTION_HARD_STOP_PREEMPTED_UNDERLYING"
OPTION_EMERGENCY_STOP_OUTCOME_STATUS = "PREEMPTED_BY_OPTION_EMERGENCY_STOP"
BROKER_PROTECTIVE_STOP_UNAVAILABLE = "BROKER_PROTECTIVE_STOP_UNAVAILABLE"


def is_option_emergency_stop(reason: object) -> bool:
    """Recognize the canonical reason and the persisted legacy alias."""
    value = str(reason or "").split(" ", 1)[0]
    return value in {OPTION_EMERGENCY_STOP, OPTION_HARD_STOP_HIT_COMPAT_ALIAS}


def canonical_option_emergency_stop(reason: object) -> str:
    return OPTION_EMERGENCY_STOP if is_option_emergency_stop(reason) else str(reason)
