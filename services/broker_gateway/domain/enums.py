"""Domain Enums for Broker Gateway.

Clean Architecture: Contains pure domain enumerations with zero framework or external dependencies.
"""

from __future__ import annotations

from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    NFO = "NFO"
    BSE = "BSE"


class ProductType(str, Enum):
    CASH = "CASH"
    OPTIONS = "OPTIONS"
    FUTURES = "FUTURES"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStyle(str, Enum):
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    AGGRESSIVE_LIMIT = "AGGRESSIVE_LIMIT"


class OptionRight(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class OrderValidity(str, Enum):
    DAY = "DAY"
    IOC = "IOC"


class BrokerWriteAction(str, Enum):
    PLACE = "PLACE"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    SQUARE_OFF = "SQUARE_OFF"


class BrokerWriteStatus(str, Enum):
    RECEIVED = "RECEIVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    REJECTED = "REJECTED"
    FAILED_SAFE = "FAILED_SAFE"


class SessionStatus(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    NEEDS_SESSION = "NEEDS_SESSION"
    ACTIVATING = "ACTIVATING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    DISCONNECTED = "DISCONNECTED"


class FeedInterval(str, Enum):
    ONE_SECOND = "1second"
    ONE_MINUTE = "1minute"
    FIVE_MINUTE = "5minute"
    THIRTY_MINUTE = "30minute"
    ONE_DAY = "1day"

