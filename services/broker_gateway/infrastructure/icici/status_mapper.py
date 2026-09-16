"""Breeze Order Status Normalizer.

Maps Breeze raw status strings to canonical platform OrderState.
Preserves both raw broker status and normalized platform status.
"""

from __future__ import annotations

from libs.contracts.models import OrderState

# Mapping of lowercase Breeze status words to canonical domain OrderState
_BREEZE_STATUS_MAP: dict[str, OrderState] = {
    "ordered": OrderState.ACKNOWLEDGED,
    "requested": OrderState.ACKNOWLEDGED,
    "queued": OrderState.ACKNOWLEDGED,
    "open": OrderState.OPEN,
    "part executed": OrderState.PARTIALLY_FILLED,
    "partially executed": OrderState.PARTIALLY_FILLED,
    "executed": OrderState.FILLED,
    "complete": OrderState.FILLED,
    "cancelled": OrderState.CANCELLED,
    "canceled": OrderState.CANCELLED,
    "rejected": OrderState.REJECTED,
    "expired": OrderState.EXPIRED,
}



def normalize_breeze_order_status(raw_status: str) -> OrderState:
    """Normalize Breeze order status string into canonical OrderState enum."""
    if not raw_status:
        return OrderState.SUBMISSION_UNKNOWN
    normalized_key = raw_status.strip().lower()
    return _BREEZE_STATUS_MAP.get(normalized_key, OrderState.SUBMISSION_UNKNOWN)
