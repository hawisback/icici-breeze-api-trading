"""Breeze Response Validator and Envelope Normalizer.

Ensures that Breeze dictionary envelopes ({'Success': ..., 'Status': ..., 'Error': ...})
never leak beyond the infrastructure layer, and translates broker errors into domain exceptions.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from services.broker_gateway.domain.errors import (
    BrokerAuthenticationError,
    BrokerBaseError,
    BrokerOrderRejectedError,
    BrokerOrderStateConflictError,
    BrokerProtocolError,
    BrokerRateLimitError,
    BrokerSessionExpiredError,
    BrokerUnavailableError,
    BrokerUnknownError,
    BrokerValidationError,
)

logger = logging.getLogger(__name__)


class BreezeResponseValidator:
    """Validates raw dictionary responses returned by the Breeze SDK."""

    @staticmethod
    def validate_and_extract(raw_response: Any, operation: str = "operation") -> Any:
        """Validate raw response envelope and extract the inner 'Success' payload.

        Args:
            raw_response: The dictionary returned by the Breeze SDK method.
            operation: Name of the operation for contextual logging.

        Returns:
            The contents of 'Success' (dict, list, or primitive).

        Raises:
            BrokerBaseError: Typed domain error if the envelope signals failure.
        """
        if raw_response is None:
            raise BrokerProtocolError(f"Breeze SDK returned None for {operation}")

        if not isinstance(raw_response, dict):
            # Some methods may return lists directly
            return raw_response

        # Check status code in envelope
        status = raw_response.get("Status")
        error_msg = raw_response.get("Error") or raw_response.get("error") or raw_response.get("message")
        success_payload = raw_response.get("Success")

        # Convert status to integer if string (e.g. "200" -> 200)
        status_code: Optional[int] = None
        if status is not None:
            try:
                status_code = int(status)
            except (ValueError, TypeError):
                status_code = None

        # Check for error condition
        is_error = False
        if status_code is not None and status_code >= 400:
            is_error = True
        elif error_msg and not success_payload:
            is_error = True

        if is_error:
            msg = str(error_msg or f"Breeze {operation} failed with status {status}")
            msg_lower = msg.lower()

            logger.error("Breeze %s error response: %s (status=%s)", operation, msg, status)

            if any(term in msg_lower for term in ["session", "token expired", "key expired", "invalid session"]):
                raise BrokerSessionExpiredError(msg, raw_response=raw_response)
            if any(term in msg_lower for term in ["auth", "unauthorized", "invalid appkey", "invalid secretkey", "api key"]):
                raise BrokerAuthenticationError(msg, raw_response=raw_response)
            if status_code == 429 or any(term in msg_lower for term in ["rate limit", "request limit", "limit exceeded", "exceeded", "too many requests", "throttle"]):
                raise BrokerRateLimitError(msg, raw_response=raw_response)

            if any(term in msg_lower for term in ["rejected", "rms", "margin", "funds insufficient", "circuit"]):
                raise BrokerOrderRejectedError(msg, raw_response=raw_response)
            if any(term in msg_lower for term in ["already executed", "cannot modify", "cannot cancel", "terminal"]):
                raise BrokerOrderStateConflictError(msg, raw_response=raw_response)
            if any(term in msg_lower for term in ["unavailable", "down", "maintenance", "server error", "503"]):
                raise BrokerUnavailableError(msg, raw_response=raw_response)
            if any(term in msg_lower for term in ["validation", "invalid parameter", "strike", "contract"]):
                raise BrokerValidationError(msg, raw_response=raw_response)

            raise BrokerUnknownError(msg, raw_response=raw_response)

        return success_payload if success_payload is not None else raw_response

    # Convenient alias
    unwrap_success = validate_and_extract

