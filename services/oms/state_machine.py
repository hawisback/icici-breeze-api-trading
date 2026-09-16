"""Canonical 14-state Order Management System (OMS) state machine.
"""

from __future__ import annotations

from typing import Optional

from libs.contracts.models import OrderState


class InvalidOrderStateTransitionError(Exception):
    """Raised when an illegal transition is attempted in the OMS state machine."""

    def __init__(self, from_state: OrderState, to_state: OrderState, reason: Optional[str] = None) -> None:
        msg = f"Invalid order state transition from {from_state} to {to_state}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason


class OrderStateMachine:
    """Enforces deterministic, audited transitions according to the trading specification."""

    VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
        OrderState.CREATED: {
            OrderState.VALIDATING,
            OrderState.REJECTED,
            OrderState.FAILED_SAFE,
        },
        OrderState.VALIDATING: {
            OrderState.APPROVED,
            OrderState.RISK_REJECTED,
            OrderState.FAILED_SAFE,
        },
        OrderState.RISK_REJECTED: set(),  # Terminal state
        OrderState.APPROVED: {
            OrderState.SUBMITTING,
            OrderState.ACKNOWLEDGED,
            OrderState.OPEN,
            OrderState.FILLED,
            OrderState.SUBMISSION_UNKNOWN,
            OrderState.CANCELLED,
            OrderState.FAILED_SAFE,
        },
        OrderState.SUBMITTING: {
            OrderState.ACKNOWLEDGED,
            OrderState.OPEN,
            OrderState.FILLED,
            OrderState.PARTIALLY_FILLED,
            OrderState.REJECTED,
            OrderState.SUBMISSION_UNKNOWN,  # Crucial: on network timeout
            OrderState.FAILED_SAFE,
        },
        OrderState.SUBMISSION_UNKNOWN: {
            OrderState.ACKNOWLEDGED,
            OrderState.OPEN,
            OrderState.FILLED,
            OrderState.PARTIALLY_FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.FAILED_SAFE,
        },
        OrderState.ACKNOWLEDGED: {
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.FAILED_SAFE,
        },
        OrderState.OPEN: {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.FAILED_SAFE,
        },
        OrderState.PARTIALLY_FILLED: {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.FAILED_SAFE,
        },
        OrderState.FILLED: set(),  # Terminal state
        OrderState.CANCELLED: set(),  # Terminal state
        OrderState.REJECTED: set(),  # Terminal state
        OrderState.EXPIRED: set(),  # Terminal state
        OrderState.FAILED_SAFE: set(),  # Terminal safety state
    }

    @classmethod
    def can_transition(cls, from_state: OrderState, to_state: OrderState) -> bool:
        return to_state in cls.VALID_TRANSITIONS.get(from_state, set())

    @classmethod
    def validate_transition(
        cls,
        from_state: OrderState,
        to_state: OrderState,
        reason: Optional[str] = None,
    ) -> None:
        if not cls.can_transition(from_state, to_state):
            raise InvalidOrderStateTransitionError(from_state, to_state, reason)
