"""Service Migration Registry.

Defines the isolated SQLite database paths and migration directories for all services.
Enforces the core architectural invariant:
Each service strictly owns its own SQLite database file and Alembic migration history.
No service opens or migrates another service's database.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from libs.config.settings import PlatformSettings, get_platform_settings


@dataclass(frozen=True)
class ServiceMigrationInfo:
    """Metadata describing a microservice's database and migration environment."""

    name: str
    description: str
    migrations_dir: Path
    db_path_resolver: Callable[[PlatformSettings], Path]

    def get_db_path(self, settings: Optional[PlatformSettings] = None) -> Path:
        s = settings or get_platform_settings()
        return self.db_path_resolver(s)


BASE_DIR = Path(__file__).resolve().parent.parent.parent


SERVICES: dict[str, ServiceMigrationInfo] = {
    "oms": ServiceMigrationInfo(
        name="oms",
        description="Order Management System (order intents, broker orders, order lifecycle events)",
        migrations_dir=BASE_DIR / "services" / "oms" / "migrations",
        db_path_resolver=lambda s: s.oms_db_path,
    ),
    "risk": ServiceMigrationInfo(
        name="risk",
        description="Risk Management Service (system modes, kill switch events, risk decisions)",
        migrations_dir=BASE_DIR / "services" / "risk" / "migrations",
        db_path_resolver=lambda s: s.risk_db_path,
    ),
    "portfolio": ServiceMigrationInfo(
        name="portfolio",
        description="Portfolio & Positions Service (executions, positions, P&L snapshots)",
        migrations_dir=BASE_DIR / "services" / "portfolio" / "migrations",
        db_path_resolver=lambda s: s.portfolio_db_path,
    ),
    "instrument": ServiceMigrationInfo(
        name="instrument",
        description="Instrument Master Service (contracts, strike lookup, broker tokens)",
        migrations_dir=BASE_DIR / "services" / "instrument" / "migrations",
        db_path_resolver=lambda s: s.instruments_db_path,
    ),
    "broker_session": ServiceMigrationInfo(
        name="broker_session",
        description="Broker Session Service (accounts, masked session history, health checks)",
        migrations_dir=BASE_DIR / "services" / "broker_session" / "migrations",
        db_path_resolver=lambda s: s.broker_session_db_path,
    ),
    "historical": ServiceMigrationInfo(
        name="historical",
        description="Historical Market Data Service (OHLCV candlestick records, intervals)",
        migrations_dir=BASE_DIR / "services" / "historical" / "migrations",
        db_path_resolver=lambda s: s.historical_db_path,
    ),
    "audit": ServiceMigrationInfo(
        name="audit",
        description="Append-only Audit Service (security, command, and regulatory events)",
        migrations_dir=BASE_DIR / "services" / "audit" / "migrations",
        db_path_resolver=lambda s: s.audit_db_path,
    ),
    "strategy": ServiceMigrationInfo(
        name="strategy",
        description="Strategy Service (strategy definitions, running instances, generated signals)",
        migrations_dir=BASE_DIR / "services" / "strategy" / "migrations",
        db_path_resolver=lambda s: s.strategy_db_path,
    ),
    "auth": ServiceMigrationInfo(
        name="auth",
        description="Authentication & Authorization Service (users, refresh tokens, WS tickets)",
        migrations_dir=BASE_DIR / "services" / "auth" / "migrations",
        db_path_resolver=lambda s: s.auth_db_path,
    ),
    "gateway": ServiceMigrationInfo(
        name="gateway",
        description="API Gateway Boundary Service (command idempotency records)",
        migrations_dir=BASE_DIR / "services" / "api_gateway" / "migrations",
        db_path_resolver=lambda s: s.gateway_db_path,
    ),
    "broker_gateway": ServiceMigrationInfo(
        name="broker_gateway",
        description="Broker Gateway Service (broker write requests, call audit, subscriptions)",
        migrations_dir=BASE_DIR / "services" / "broker_gateway" / "migrations",
        db_path_resolver=lambda s: s.broker_gateway_db_path,
    ),
}


def get_registered_services() -> list[str]:
    """Return all registered service names in canonical order."""
    return list(SERVICES.keys())


def get_service_info(service_name: str) -> ServiceMigrationInfo:
    """Retrieve metadata for a registered service or raise ValueError."""
    if service_name not in SERVICES:
        known = ", ".join(SERVICES.keys())
        raise ValueError(f"Unknown service '{service_name}'. Registered services: {known}")
    return SERVICES[service_name]


def resolve_service_db_path(
    service_name: str,
    settings: Optional[PlatformSettings] = None,
    custom_db_path: Optional[Path] = None,
) -> Path:
    """Resolve database path for a service, honoring explicit override if provided."""
    if custom_db_path is not None:
        return custom_db_path.resolve()
    info = get_service_info(service_name)
    return info.get_db_path(settings).resolve()

