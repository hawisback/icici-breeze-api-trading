"""Service container orchestrating all microservices dependencies for the API Gateway.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Optional

from libs.config.settings import BrokerBackend, MarketDataBackend, PlatformSettings, get_platform_settings
from libs.contracts.models import SystemMode, TradingMode
from libs.events.bus import EventBus, InMemoryEventBus, get_event_bus
from services.audit.repository import AuditRepository
from services.audit.service import AuditService
from services.auth.repository import AuthRepository
from services.auth.service import AuthService
from services.broker_gateway.service import BrokerGatewayService
from services.broker_session.repository import BrokerSessionRepository
from services.broker_session.service import BrokerSessionService
from services.execution.service import ExecutionService
from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService
from services.instrument.repository import InstrumentRepository
from services.instrument.service import InstrumentService
from services.market_data.service import MarketDataService
from services.oms.repository import OMSRepository
from services.oms.service import OMSService
from services.option_chain.service import OptionChainService
from services.portfolio.repository import PortfolioRepository
from services.portfolio.service import PortfolioService
from services.risk.live_gate import LiveTradingGate
from services.risk.repository import RiskRepository
from services.risk.service import RiskService
from services.strategy.repository import StrategyRepository
from services.strategy.service import StrategyService
from services.api_gateway.idempotency import IdempotencyRepository
from services.api_gateway.rate_limiter import RateLimiter
from infra.migrations.registry import get_registered_services
from infra.migrations.runner import assert_service_compatibility, upgrade_all_services

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    settings: PlatformSettings
    event_bus: EventBus
    session_svc: BrokerSessionService
    gateway_svc: BrokerGatewayService
    instrument_svc: InstrumentService
    market_svc: MarketDataService
    historical_svc: HistoricalService
    option_chain_svc: OptionChainService
    oms_svc: OMSService
    risk_svc: RiskService
    live_gate: LiveTradingGate
    exec_svc: ExecutionService
    portfolio_svc: PortfolioService
    strategy_svc: StrategyService
    audit_svc: AuditService
    auth_svc: AuthService
    idempotency_repo: IdempotencyRepository
    rate_limiter: RateLimiter


_container: Optional[ServiceContainer] = None


async def initialize_services(
    settings: Optional[PlatformSettings] = None,
    force_reinit: bool = False,
) -> ServiceContainer:
    """Initialize and wire all platform microservices using typed settings."""
    global _container
    if _container is not None and not force_reinit and settings is None:
        return _container

    if _container is not None:
        try:
            if hasattr(_container.oms_svc, "_outbox_worker_task") and _container.oms_svc._outbox_worker_task:
                _container.oms_svc._outbox_worker_task.cancel()
            if hasattr(_container.exec_svc, "_reconciliation_task") and _container.exec_svc._reconciliation_task:
                _container.exec_svc._reconciliation_task.cancel()
            if hasattr(_container.risk_svc, "_outbox_worker_task") and _container.risk_svc._outbox_worker_task:
                _container.risk_svc._outbox_worker_task.cancel()
            if hasattr(_container.market_svc, "_feed_task") and _container.market_svc._feed_task:
                _container.market_svc._feed_task.cancel()
        except Exception:
            pass
        _container = None

    app_settings = settings or get_platform_settings()
    logger.info("Initializing services with config: %s", app_settings.get_redacted_summary())

    # Database migrations and fail-closed schema compatibility check
    if app_settings.auto_migrate_on_startup:
        logger.info("Executing auto-migration for all microservices to head revision...")
        upgrade_all_services(settings=app_settings)
    else:
        logger.info("Performing fail-closed schema compatibility check on all microservices...")
        for svc_name in get_registered_services():
            assert_service_compatibility(svc_name, settings=app_settings)

    bus = InMemoryEventBus()
    await bus.start()

    session_repo = BrokerSessionRepository(db_path=app_settings.broker_session_db_path)
    session_svc = BrokerSessionService(repository=session_repo, event_bus=bus)
    await session_svc.initialize()

    gateway_svc = BrokerGatewayService(settings=app_settings)
    await gateway_svc.initialize()
    session_svc.set_broker_gateway(gateway_svc)

    # Broker sessions are independent from execution ownership. Activate every
    # configured daily session so routing can use Kite and Breeze concurrently.
    kite_key = (
        app_settings.kite_api_key.get_secret_value()
        if app_settings.kite_api_key
        else ""
    )
    kite_secret = (
        app_settings.kite_api_secret.get_secret_value()
        if app_settings.kite_api_secret
        else ""
    )
    kite_request = (
        app_settings.kite_request_token.get_secret_value()
        if app_settings.kite_request_token
        else ""
    )
    kite_access = (
        app_settings.kite_access_token.get_secret_value()
        if app_settings.kite_access_token
        else ""
    )
    if kite_key and kite_secret and (kite_request or kite_access):
        try:
            logger.info("Attempting auto-activation of configured Kite session...")
            await session_svc.activate_session(
                api_key=kite_key,
                secret_key=kite_secret,
                session_token=kite_request,
                access_token=kite_access or None,
                account_id="ZERODHA_PRIMARY",
                broker_backend="kite",
            )
        except Exception as exc:
            logger.warning("Startup Kite auto-activation deferred: %s", exc)

    if (
        app_settings.breeze_api_key
        and app_settings.breeze_secret_key
        and app_settings.breeze_session_token
    ):
        try:
            api_key_val = app_settings.breeze_api_key.get_secret_value()
            tok_val = app_settings.breeze_session_token.get_secret_value()
            if tok_val and tok_val != "your_daily_session_token_here":
                logger.info("Attempting auto-activation of configured Breeze session...")
                await session_svc.activate_session(
                    api_key=api_key_val,
                    secret_key=app_settings.breeze_secret_key.get_secret_value(),
                    session_token=tok_val,
                    account_id="ICICI_PRIMARY",
                    broker_backend="breeze",
                )
        except Exception as exc:
            logger.warning("Startup Breeze auto-activation deferred: %s", exc)

    instrument_repo = InstrumentRepository(db_path=app_settings.instruments_db_path)
    instrument_svc = InstrumentService(repository=instrument_repo)
    await instrument_svc.initialize()

    market_svc = MarketDataService(event_bus=bus, broker_gateway=gateway_svc)
    await market_svc.initialize()
    await market_svc.start_feed_loop(interval_sec=2.5)

    historical_repo = HistoricalRepository(db_path=app_settings.historical_db_path)
    historical_svc = HistoricalService(repository=historical_repo, broker_gateway=gateway_svc, instrument_service=instrument_svc)
    await historical_svc.initialize()

    option_chain_svc = OptionChainService(
        instrument_service=instrument_svc,
        market_data_service=market_svc,
        broker_gateway=gateway_svc,
    )

    oms_repo = OMSRepository(db_path=app_settings.oms_db_path)
    oms_svc = OMSService(repository=oms_repo, event_bus=bus)
    await oms_svc.initialize()
    await oms_svc.start_outbox_worker(poll_interval_sec=0.2)

    live_gate = LiveTradingGate(settings=app_settings, event_bus=bus)

    live_account_id = (
        "ZERODHA_PRIMARY"
        if app_settings.broker_backend == BrokerBackend.KITE
        else "ICICI_PRIMARY"
    )

    portfolio_repo = PortfolioRepository(db_path=app_settings.portfolio_db_path)
    portfolio_svc = PortfolioService(
        repository=portfolio_repo, market_data_service=market_svc, event_bus=bus
    )
    await portfolio_svc.initialize()

    risk_repo = RiskRepository(db_path=app_settings.risk_db_path)
    risk_svc = RiskService(
        repository=risk_repo,
        event_bus=bus,
        live_gate=live_gate,
        max_order_qty=1800,
        live_account_id=live_account_id,
        portfolio_service=portfolio_svc,
        broker_gateway=gateway_svc,
        broker_session_service=session_svc,
        market_data_service=market_svc,
        live_max_order_notional=app_settings.live_max_order_notional,
        live_max_open_positions=app_settings.live_max_open_positions,
        live_market_data_max_age_seconds=(
            app_settings.live_market_data_max_age_seconds
        ),
    )
    await risk_svc.initialize()

    exec_svc = ExecutionService(
        broker_gateway=gateway_svc,
        oms_service=oms_svc,
        live_gate=live_gate,
        event_bus=bus,
        live_account_id=live_account_id,
        broker_session_service=session_svc,
        market_data_service=market_svc,
        portfolio_service=portfolio_svc,
        live_market_data_max_age_seconds=(
            app_settings.live_market_data_max_age_seconds
        ),
    )
    await exec_svc.initialize()

    strategy_repo = StrategyRepository(db_path=app_settings.strategy_db_path)
    strategy_svc = StrategyService(
        repository=strategy_repo,
        oms_service=oms_svc,
        event_bus=bus,
        option_chain_service=option_chain_svc,
        market_data_service=market_svc,
        historical_service=historical_svc,
    )
    await strategy_svc.initialize()

    # Startup reconciliation never grants authority. It only compares durable
    # local LIVE state with broker truth and, on verified inconsistencies,
    # blocks new entries until an operator resolves them.
    startup_orders = await oms_svc.list_orders(limit=500)
    startup_broker_positions = []
    startup_broker_verified = False
    startup_broker_error = None
    startup_session = await session_svc.get_session_status()
    if startup_session.get("connected"):
        try:
            startup_broker_positions = await gateway_svc.get_positions(
                mode=TradingMode.LIVE
            )
            startup_broker_verified = True
        except Exception as exc:
            startup_broker_error = type(exc).__name__
            logger.exception(
                "Startup LIVE broker position reconciliation failed"
            )
    else:
        startup_broker_error = "BROKER_SESSION_NOT_CONNECTED"

    startup_reconciliation = (
        await strategy_svc.build_live_reconciliation_report(
            broker_positions=startup_broker_positions,
            orders=startup_orders,
            broker_verified=startup_broker_verified,
            broker_error=startup_broker_error,
            record_as_startup=True,
        )
    )
    if (
        app_settings.live_trading_enabled
        and startup_broker_verified
        and startup_reconciliation.get("issues")
    ):
        current_risk_mode = await risk_svc.get_system_mode()
        if current_risk_mode == SystemMode.NORMAL:
            await risk_svc.set_system_mode(SystemMode.ENTRY_BLOCKED)
            logger.warning(
                "LIVE startup reconciliation found inconsistencies; "
                "Risk mode moved to ENTRY_BLOCKED."
            )

    audit_repo = AuditRepository(db_path=app_settings.audit_db_path)
    audit_svc = AuditService(repository=audit_repo, event_bus=bus)
    await audit_svc.initialize()

    auth_repo = AuthRepository(db_path=app_settings.auth_db_path, settings=app_settings)
    auth_svc = AuthService(repository=auth_repo, event_bus=bus, settings=app_settings)
    await auth_svc.initialize()

    idempotency_repo = IdempotencyRepository(db_path=app_settings.gateway_db_path)
    await idempotency_repo.initialize()

    rate_limiter = RateLimiter(settings=app_settings)

    _container = ServiceContainer(
        settings=app_settings,
        event_bus=bus,
        session_svc=session_svc,
        gateway_svc=gateway_svc,
        instrument_svc=instrument_svc,
        market_svc=market_svc,
        historical_svc=historical_svc,
        option_chain_svc=option_chain_svc,
        oms_svc=oms_svc,
        risk_svc=risk_svc,
        live_gate=live_gate,
        exec_svc=exec_svc,
        portfolio_svc=portfolio_svc,
        strategy_svc=strategy_svc,
        audit_svc=audit_svc,
        auth_svc=auth_svc,
        idempotency_repo=idempotency_repo,
        rate_limiter=rate_limiter,
    )

    logger.info("All microservices initialized successfully.")
    return _container


async def ensure_services() -> ServiceContainer:
    global _container
    if _container is None:
        return await initialize_services()
    return _container


def get_services() -> ServiceContainer:
    if _container is None:
        raise RuntimeError("ServiceContainer has not been initialized. Ensure initialize_services() has been called.")
    return _container
