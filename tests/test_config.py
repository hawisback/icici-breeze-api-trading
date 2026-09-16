"""Unit tests verifying typed configuration, safety validation, and secret redaction."""

from pathlib import Path
import pytest
from pydantic import SecretStr

from libs.config.settings import (
    AppEnv,
    EventBusBackend,
    MarketDataBackend,
    PlatformSettings,
)
from libs.contracts.models import TradingMode


def test_default_settings():
    """Verify safe defaults: PAPER mode, memory bus, simulated market, no live trading."""
    settings = PlatformSettings(_env_file=None)
    assert settings.default_trading_mode == TradingMode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.event_bus_backend == EventBusBackend.MEMORY
    assert settings.market_data_backend == MarketDataBackend.SIMULATED
    assert settings.app_env == AppEnv.DEVELOPMENT
    assert "http://localhost:3000" in settings.cors_allowed_origins


def test_reject_live_as_default_mode():
    """Verify DEFAULT_TRADING_MODE=LIVE is rejected with a validation error."""
    with pytest.raises(ValueError, match="DEFAULT_TRADING_MODE cannot be set to LIVE"):
        PlatformSettings(default_trading_mode=TradingMode.LIVE)


def test_reject_wildcard_cors():
    """Verify wildcard '*' in CORS is rejected for security."""
    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS cannot contain wildcard"):
        PlatformSettings(cors_allowed_origins=["*"])


def test_reject_redpanda_without_brokers():
    """Verify redpanda backend requires brokers configuration."""
    with pytest.raises(ValueError, match="REDPANDA_BROKERS must be configured"):
        PlatformSettings(event_bus_backend=EventBusBackend.REDPANDA, redpanda_brokers=None)


def test_reject_unsupported_production_configs():
    """Verify production rejects in-memory eventing, simulated feeds, and missing signing keys."""
    # Production with in-memory bus rejected
    with pytest.raises(ValueError, match="In-memory event bus is prohibited in production"):
        PlatformSettings(
            app_env=AppEnv.PRODUCTION,
            event_bus_backend=EventBusBackend.MEMORY,
            auth_signing_key=SecretStr("super_secret_signing_key_12345"),
            market_data_backend=MarketDataBackend.BREEZE,
            breeze_api_key=SecretStr("k"),
            breeze_secret_key=SecretStr("s"),
        )

    # Production with simulated market data rejected
    with pytest.raises(ValueError, match="Simulated market data is prohibited in production"):
        PlatformSettings(
            app_env=AppEnv.PRODUCTION,
            event_bus_backend=EventBusBackend.REDPANDA,
            redpanda_brokers="localhost:9092",
            auth_signing_key=SecretStr("super_secret_signing_key_12345"),
            market_data_backend=MarketDataBackend.SIMULATED,
        )

    # Production without auth signing key rejected
    with pytest.raises(ValueError, match="AUTH_SIGNING_KEY is mandatory in production"):
        PlatformSettings(
            app_env=AppEnv.PRODUCTION,
            event_bus_backend=EventBusBackend.REDPANDA,
            redpanda_brokers="localhost:9092",
            market_data_backend=MarketDataBackend.BREEZE,
            breeze_api_key=SecretStr("k"),
            breeze_secret_key=SecretStr("s"),
            auth_signing_key=None,
        )


def test_breeze_backend_requires_credentials():
    """Verify breeze market data backend requires API key and secret key."""
    with pytest.raises(ValueError, match="BREEZE_API_KEY and BREEZE_SECRET_KEY are required"):
        PlatformSettings(
            market_data_backend=MarketDataBackend.BREEZE,
            breeze_api_key=None,
        )


def test_secrets_redaction():
    """Verify secrets are never leaked in string representation or diagnostic summary."""
    raw_secret = "super_secret_token_abcdef123456"
    settings = PlatformSettings(
        breeze_api_key=SecretStr(raw_secret),
        breeze_secret_key=SecretStr(raw_secret),
        auth_signing_key=SecretStr(raw_secret),
    )

    # In str / repr, secrets are hidden
    settings_str = str(settings)
    assert raw_secret not in settings_str

    # In diagnostic summary, values are masked as [CONFIGURED]
    summary = settings.get_redacted_summary()
    assert summary["breeze_api_key"] == "[CONFIGURED]"
    assert summary["breeze_secret_key"] == "[CONFIGURED]"
    assert summary["auth_signing_key"] == "[CONFIGURED]"
    assert raw_secret not in str(summary)


def test_safe_database_path_derivation(tmp_path):
    """Verify per-service SQLite database paths are safely resolved under DATA_ROOT."""
    settings = PlatformSettings(data_root=tmp_path)
    assert settings.oms_db_path == tmp_path / "oms" / "oms.db"
    assert settings.risk_db_path == tmp_path / "risk" / "risk.db"
    assert settings.portfolio_db_path == tmp_path / "portfolio" / "portfolio.db"
    assert settings.broker_session_db_path == tmp_path / "broker-session" / "broker_session.db"
    assert settings.instruments_db_path == tmp_path / "instruments" / "instruments.db"

