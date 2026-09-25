"""Typed configuration, environment settings, and fail-closed validation.

Enforces:
- Default execution mode is PAPER. LIVE cannot be the default.
- Fail-closed validation for production and broker configurations.
- Exclusion of secrets from logs, repr, and serialization.
- Safe derivation of service SQLite database paths from DATA_ROOT.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.contracts.models import TradingMode


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class EventBusBackend(str, Enum):
    MEMORY = "memory"
    REDPANDA = "redpanda"


class MarketDataBackend(str, Enum):
    SIMULATED = "simulated"
    BREEZE = "breeze"
    KITE = "kite"
    HYBRID = "hybrid"


class BrokerBackend(str, Enum):
    """Live broker selected for order/session operations."""

    BREEZE = "breeze"
    KITE = "kite"


class PlatformSettings(BaseSettings):
    """Central typed platform settings with fail-closed safety validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Environment
    app_env: AppEnv = Field(default=AppEnv.DEVELOPMENT, alias="APP_ENV")
    service_name: str = Field(default="trading-platform", alias="SERVICE_NAME")
    data_root: Path = Field(default=Path("./data"), alias="DATA_ROOT")
    trading_config_path: Path = Field(
        default=Path("./config/trading.json"),
        alias="TRADING_CONFIG_PATH",
        description="Canonical non-secret operator trading configuration file",
    )

    # Eventing
    event_bus_backend: EventBusBackend = Field(default=EventBusBackend.MEMORY, alias="EVENT_BUS_BACKEND")
    redpanda_brokers: Optional[str] = Field(default=None, alias="REDPANDA_BROKERS")
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    # Trading Authority & Safety
    default_trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="DEFAULT_TRADING_MODE")
    local_single_user_mode: bool = Field(
        default=False,
        alias="LOCAL_SINGLE_USER_MODE",
        description=(
            "Local workstation mode: operator/LIVE confirmation UX may be "
            "bypassed only when the API is bound to loopback."
        ),
    )
    live_trading_enabled: bool = Field(default=False, alias="LIVE_TRADING_ENABLED")
    live_allowed_accounts: list[str] = Field(default_factory=list, alias="LIVE_ALLOWED_ACCOUNTS")
    live_max_order_notional: float = Field(
        default=50000.0,
        gt=0,
        alias="LIVE_MAX_ORDER_NOTIONAL",
    )
    live_max_open_positions: int = Field(
        default=1,
        ge=1,
        le=10,
        alias="LIVE_MAX_OPEN_POSITIONS",
    )
    live_market_data_max_age_seconds: float = Field(
        default=5.0,
        gt=0.5,
        le=30.0,
        alias="LIVE_MARKET_DATA_MAX_AGE_SECONDS",
    )

    # API & Network & Boundary Security
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        alias="CORS_ALLOWED_ORIGINS",
    )
    max_request_body_bytes: int = Field(default=1_048_576, alias="MAX_REQUEST_BODY_BYTES")
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_login_per_minute: int = Field(default=60, alias="RATE_LIMIT_LOGIN_PER_MINUTE")
    rate_limit_orders_per_minute: int = Field(default=60, alias="RATE_LIMIT_ORDERS_PER_MINUTE")
    rate_limit_general_per_minute: int = Field(default=120, alias="RATE_LIMIT_GENERAL_PER_MINUTE")
    idempotency_ttl_seconds: int = Field(default=86400, alias="IDEMPOTENCY_TTL_SECONDS")

    # Broker execution ownership and data/API routing are intentionally separate.
    market_data_backend: MarketDataBackend = Field(
        default=MarketDataBackend.SIMULATED,
        alias="MARKET_DATA_BACKEND",
    )
    broker_backend: BrokerBackend = Field(
        default=BrokerBackend.BREEZE,
        validation_alias=AliasChoices(
            "broker_backend",
            "LIVE_EXECUTION_BROKER",
            "BROKER_BACKEND",
        ),
        serialization_alias="LIVE_EXECUTION_BROKER",
        description=(
            "Broker that owns the complete LIVE order lifecycle. BROKER_BACKEND "
            "remains accepted as a legacy environment alias."
        ),
    )
    frequent_data_broker: BrokerBackend = Field(
        default=BrokerBackend.KITE,
        alias="FREQUENT_DATA_BROKER",
        description="Provider for high-frequency quotes and candle refreshes.",
    )
    reference_data_broker: BrokerBackend = Field(
        default=BrokerBackend.BREEZE,
        alias="REFERENCE_DATA_BROKER",
        description="Provider for lower-frequency/reference data such as option-chain Greeks.",
    )

    # Secrets (strictly redacted by SecretStr)
    breeze_api_key: Optional[SecretStr] = Field(default=None, alias="BREEZE_API_KEY")
    breeze_secret_key: Optional[SecretStr] = Field(default=None, alias="BREEZE_SECRET_KEY")
    breeze_session_token: Optional[SecretStr] = Field(default=None, alias="BREEZE_SESSION_TOKEN")
    kite_api_key: Optional[SecretStr] = Field(default=None, alias="KITE_API_KEY")
    kite_api_secret: Optional[SecretStr] = Field(default=None, alias="KITE_API_SECRET")
    kite_request_token: Optional[SecretStr] = Field(default=None, alias="KITE_REQUEST_TOKEN")
    kite_access_token: Optional[SecretStr] = Field(default=None, alias="KITE_ACCESS_TOKEN")
    kite_product: str = Field(default="NRML", alias="KITE_PRODUCT")
    auth_signing_key: Optional[SecretStr] = Field(default=None, alias="AUTH_SIGNING_KEY")

    # Auth & Tokens
    access_token_expire_minutes: int = Field(default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")
    ws_ticket_expire_seconds: int = Field(default=60, alias="WS_TICKET_EXPIRE_SECONDS")

    # ICICI Breeze Rate Limits (with operational safety headroom)
    breeze_calls_per_minute: int = Field(default=90, alias="BREEZE_CALLS_PER_MINUTE")
    breeze_calls_per_day: int = Field(default=4800, alias="BREEZE_CALLS_PER_DAY")
    breeze_writes_per_second: int = Field(default=8, alias="BREEZE_WRITES_PER_SECOND")

    # Database Migrations & Safety
    auto_migrate_on_startup: bool = Field(default=True, alias="AUTO_MIGRATE_ON_STARTUP")
    migration_backup_dir: Path = Field(default=Path("data/backups"), alias="MIGRATION_BACKUP_DIR")

    # Observability
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("default_trading_mode")
    @classmethod
    def validate_default_trading_mode(cls, v: TradingMode) -> TradingMode:
        if v == TradingMode.LIVE:
            raise ValueError(
                "DEFAULT_TRADING_MODE cannot be set to LIVE. Platform must default to PAPER."
            )
        return v

    @model_validator(mode="after")
    def validate_safety_invariants(self) -> PlatformSettings:
        # Resolve filesystem-backed configuration/data paths.
        self.data_root = self.data_root.resolve()
        self.trading_config_path = self.trading_config_path.resolve()

        if (
            self.local_single_user_mode
            and self.api_host.strip().lower()
            not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError(
                "LOCAL_SINGLE_USER_MODE=true requires API_HOST to be loopback "
                "(127.0.0.1, localhost, or ::1)."
            )

        # Reject wildcard CORS if credentials or production
        if any(origin.strip() == "*" for origin in self.cors_allowed_origins):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS cannot contain wildcard '*' as it is unsafe with credentials."
            )

        # Redpanda requires brokers
        if self.event_bus_backend == EventBusBackend.REDPANDA and not self.redpanda_brokers:
            raise ValueError(
                "REDPANDA_BROKERS must be configured when EVENT_BUS_BACKEND is set to 'redpanda'."
            )

        # Any process capable of LIVE trading must carry production-grade
        # identity and real-market safeguards, even if APP_ENV was left in a
        # non-production profile.
        if self.live_trading_enabled:
            signing_key = (
                self.auth_signing_key.get_secret_value()
                if self.auth_signing_key
                else ""
            )
            if len(signing_key.encode("utf-8")) < 32:
                raise ValueError(
                    "AUTH_SIGNING_KEY must be at least 32 bytes whenever "
                    "LIVE_TRADING_ENABLED=true."
                )
            if not self.live_allowed_accounts:
                raise ValueError(
                    "LIVE_ALLOWED_ACCOUNTS must contain at least one account when live trading is enabled."
                )
            if self.market_data_backend == MarketDataBackend.SIMULATED:
                raise ValueError(
                    "Simulated market data is prohibited when LIVE_TRADING_ENABLED=true."
                )
            required_account = (
                "ZERODHA_PRIMARY"
                if self.broker_backend == BrokerBackend.KITE
                else "ICICI_PRIMARY"
            )
            if required_account not in self.live_allowed_accounts:
                raise ValueError(
                    f"LIVE_ALLOWED_ACCOUNTS must include {required_account} "
                    f"when LIVE_EXECUTION_BROKER={self.broker_backend.value}."
                )
            if not self.rate_limit_enabled:
                raise ValueError(
                    "RATE_LIMIT_ENABLED must remain true when live trading is enabled."
                )

        # Production restrictions
        if self.app_env == AppEnv.PRODUCTION:
            if self.event_bus_backend == EventBusBackend.MEMORY:
                raise ValueError(
                    "In-memory event bus is prohibited in production. Set EVENT_BUS_BACKEND=redpanda."
                )
            if self.market_data_backend == MarketDataBackend.SIMULATED:
                raise ValueError(
                    "Simulated market data is prohibited in production. Set MARKET_DATA_BACKEND to breeze, kite, or hybrid."
                )
            if not self.auth_signing_key:
                raise ValueError("AUTH_SIGNING_KEY is mandatory in production environment.")

        # Data-routing credentials. HYBRID explicitly keeps both broker
        # adapters available: Kite for frequent reads and Breeze for reference/
        # Greeks by default.
        data_brokers: set[BrokerBackend] = set()
        if self.market_data_backend == MarketDataBackend.BREEZE:
            data_brokers.add(BrokerBackend.BREEZE)
        elif self.market_data_backend == MarketDataBackend.KITE:
            data_brokers.add(BrokerBackend.KITE)
        elif self.market_data_backend == MarketDataBackend.HYBRID:
            data_brokers.update(
                {
                    self.frequent_data_broker,
                    self.reference_data_broker,
                }
            )

        if (
            BrokerBackend.BREEZE in data_brokers
            and (not self.breeze_api_key or not self.breeze_secret_key)
        ):
            raise ValueError(
                "BREEZE_API_KEY and BREEZE_SECRET_KEY are required when "
                "Breeze participates in market-data routing."
            )
        if (
            BrokerBackend.KITE in data_brokers
            and (not self.kite_api_key or not self.kite_api_secret)
        ):
            raise ValueError(
                "KITE_API_KEY and KITE_API_SECRET are required when "
                "Kite participates in market-data routing."
            )

        # The selected execution broker must always have credentials even when
        # market-data traffic is routed elsewhere.
        if self.live_trading_enabled:
            if (
                self.broker_backend == BrokerBackend.KITE
                and (not self.kite_api_key or not self.kite_api_secret)
            ):
                raise ValueError(
                    "KITE_API_KEY and KITE_API_SECRET are required when "
                    "LIVE_EXECUTION_BROKER=kite."
                )
            if (
                self.broker_backend == BrokerBackend.BREEZE
                and (not self.breeze_api_key or not self.breeze_secret_key)
            ):
                raise ValueError(
                    "BREEZE_API_KEY and BREEZE_SECRET_KEY are required when "
                    "LIVE_EXECUTION_BROKER=breeze."
                )

        if self.kite_product.upper() not in {"MIS", "NRML", "CNC"}:
            raise ValueError("KITE_PRODUCT must be one of MIS, NRML, or CNC.")

        return self

    # Safe Derived Database Paths
    def get_service_db_path(self, relative_path: str) -> Path:
        """Derive an isolated database path under DATA_ROOT."""
        full_path = self.data_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        return full_path

    @property
    def broker_session_db_path(self) -> Path:
        return self.get_service_db_path("broker-session/broker_session.db")

    @property
    def instruments_db_path(self) -> Path:
        return self.get_service_db_path("instruments/instruments.db")

    @property
    def historical_db_path(self) -> Path:
        return self.get_service_db_path("market/historical.db")

    @property
    def oms_db_path(self) -> Path:
        return self.get_service_db_path("oms/oms.db")

    @property
    def risk_db_path(self) -> Path:
        return self.get_service_db_path("risk/risk.db")

    @property
    def portfolio_db_path(self) -> Path:
        return self.get_service_db_path("portfolio/portfolio.db")

    @property
    def strategy_db_path(self) -> Path:
        return self.get_service_db_path("strategy/strategy.db")

    @property
    def audit_db_path(self) -> Path:
        return self.get_service_db_path("audit/audit.db")

    @property
    def auth_db_path(self) -> Path:
        return self.get_service_db_path("auth/auth.db")

    @property
    def gateway_db_path(self) -> Path:
        return self.get_service_db_path("gateway/gateway.db")

    @property
    def broker_gateway_db_path(self) -> Path:
        return self.get_service_db_path("broker-gateway/broker_gateway.db")

    @property
    def backups_dir(self) -> Path:
        full_path = self.data_root / "backups"
        full_path.mkdir(parents=True, exist_ok=True)
        return full_path

    def get_auth_signing_key(self) -> str:
        """Return the secret signing key or a deterministic non-production fallback."""
        if self.auth_signing_key and self.auth_signing_key.get_secret_value():
            return self.auth_signing_key.get_secret_value()
        if self.app_env == AppEnv.PRODUCTION:
            raise ValueError("AUTH_SIGNING_KEY must be explicitly configured in production.")
        return "dev-insecure-signing-secret-change-in-prod-12345"

    def get_redacted_summary(self) -> dict[str, Any]:
        """Return diagnostic configuration with all secret values redacted."""
        return {
            "app_env": self.app_env.value,
            "service_name": self.service_name,
            "data_root": str(self.data_root),
            "trading_config_path": str(self.trading_config_path),
            "event_bus_backend": self.event_bus_backend.value,
            "redpanda_brokers": self.redpanda_brokers or "[NONE]",
            "redis_url": self.redis_url or "[NONE]",
            "default_trading_mode": self.default_trading_mode.value,
            "local_single_user_mode": self.local_single_user_mode,
            "live_trading_enabled": self.live_trading_enabled,
            "live_allowed_accounts": (
                f"[CONFIGURED:{len(self.live_allowed_accounts)}]"
                if self.live_allowed_accounts
                else "[NONE]"
            ),
            "live_max_order_notional": self.live_max_order_notional,
            "live_max_open_positions": self.live_max_open_positions,
            "live_market_data_max_age_seconds": self.live_market_data_max_age_seconds,
            "cors_allowed_origins": self.cors_allowed_origins,
            "market_data_backend": self.market_data_backend.value,
            "live_execution_broker": self.broker_backend.value,
            "broker_backend": self.broker_backend.value,
            "frequent_data_broker": self.frequent_data_broker.value,
            "reference_data_broker": self.reference_data_broker.value,
            "breeze_api_key": "[CONFIGURED]" if self.breeze_api_key else "[NOT CONFIGURED]",
            "breeze_secret_key": "[CONFIGURED]" if self.breeze_secret_key else "[NOT CONFIGURED]",
            "breeze_session_token": "[CONFIGURED]" if self.breeze_session_token else "[NOT CONFIGURED]",
            "kite_api_key": "[CONFIGURED]" if self.kite_api_key else "[NOT CONFIGURED]",
            "kite_api_secret": "[CONFIGURED]" if self.kite_api_secret else "[NOT CONFIGURED]",
            "kite_request_token": "[CONFIGURED]" if self.kite_request_token else "[NOT CONFIGURED]",
            "kite_access_token": "[CONFIGURED]" if self.kite_access_token else "[NOT CONFIGURED]",
            "kite_product": self.kite_product,
            "auth_signing_key": "[CONFIGURED]" if self.auth_signing_key else "[NOT CONFIGURED]",
            "log_level": self.log_level,
        }


_global_settings: Optional[PlatformSettings] = None


def get_platform_settings() -> PlatformSettings:
    """Return application settings singleton or create default."""
    global _global_settings
    if _global_settings is None:
        _global_settings = PlatformSettings()
    return _global_settings


def set_platform_settings(settings: PlatformSettings) -> None:
    """Explicitly set settings for testing or custom composition."""
    global _global_settings
    _global_settings = settings


# Convenient alias
get_settings = get_platform_settings
