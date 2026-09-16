"""Domain Errors Taxonomy for Broker Gateway.

Clean Architecture: Normalizes broker and transport failures into explicit domain exceptions.
"""

from __future__ import annotations

from typing import Any, Optional


class BrokerBaseError(Exception):
    """Base exception for all Broker Gateway domain errors."""

    def __init__(self, message: str, raw_response: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.raw_response = raw_response


class BrokerAuthenticationError(BrokerBaseError):
    """Raised when broker credentials, API key, or session tokens fail authentication."""
    pass


class BrokerSessionExpiredError(BrokerBaseError):
    """Raised when an active broker session has expired or requires manual daily re-login."""
    pass


class BrokerAuthorizationError(BrokerBaseError):
    """Raised when an operation is forbidden for the broker account or permission level."""
    pass


class BrokerValidationError(BrokerBaseError):
    """Raised when request arguments, strikes, or quantities fail broker validation rules."""
    pass


class BrokerRateLimitError(BrokerBaseError):
    """Raised when broker rate limits (per-second, per-minute, or per-day) are exceeded."""

    def __init__(self, message: str, retry_after_sec: Optional[float] = None, raw_response: Optional[Any] = None) -> None:
        super().__init__(message, raw_response)
        self.retry_after_sec = retry_after_sec


class BrokerTimeoutError(BrokerBaseError):
    """Raised when an HTTP or network call to the broker exceeds the timeout threshold."""
    pass


class BrokerUnavailableError(BrokerBaseError):
    """Raised when ICICI Direct systems are down, undergoing maintenance, or return 503."""
    pass


class BrokerOrderRejectedError(BrokerBaseError):
    """Raised when the broker explicitly rejects an order (e.g. margin, circuit breaker)."""

    def __init__(self, message: str, rejection_code: Optional[str] = None, raw_response: Optional[Any] = None) -> None:
        super().__init__(message, raw_response)
        self.rejection_code = rejection_code


class BrokerOrderStateConflictError(BrokerBaseError):
    """Raised when attempting to modify/cancel an order that is already terminal (FILLED, CANCELLED)."""
    pass


class BrokerMarketDataError(BrokerBaseError):
    """Raised when historical, quote, or option chain data is unavailable or invalid."""
    pass


class BrokerInstrumentError(BrokerBaseError):
    """Raised when an instrument token, strike, expiry, or contract lookup fails."""
    pass


class BrokerProtocolError(BrokerBaseError):
    """Raised when the broker returns malformed JSON, unexpected envelopes, or protocol violations."""
    pass


class BrokerSubmissionUnknownError(BrokerBaseError):
    """Raised when an order placement or write command produced an ambiguous transport result.

    Mandatory Safety Rule: SUBMISSION_UNKNOWN must freeze local state and trigger reconciliation.
    Blind resubmission is strictly prohibited.
    """

    def __init__(self, message: str, request_id: Optional[str] = None, raw_response: Optional[Any] = None) -> None:
        super().__init__(message, raw_response)
        self.request_id = request_id



class BrokerUnknownError(BrokerBaseError):
    """Raised when an unclassified error occurs."""
    pass
