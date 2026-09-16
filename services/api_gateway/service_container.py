"""Service container orchestrating all microservices dependencies for the API Gateway.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Optional

from libs.config.settings import MarketDataBackend, PlatformSettings, get_platform_settings
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

    gateway_svc = BrokerGatewayService()
    await gateway_svc.initialize()
    session_svc.set_broker_gateway(gateway_svc)

    # If credentials and session token are configured in environment, auto-activate
    if (
        app_settings.breeze_api_key
        and app_settings.breeze_secret_key
        and app_settings.breeze_session_token
    ):
        try:
            tok_val = app_settings.breeze_session_token.get_secret_value()
            if tok_val and tok_val != "your_daily_session_token_here":
                logger.info("Attempting auto-activation of broker session from configured token...")
                await session_svc.activate_session(
                    api_key=app_settings.breeze_api_key,
                    secret_key=app_settings.breeze_secret_key.get_secret_value(),
                    session_token=tok_val,
                )
        except Exception as exc:
            logger.warning("Startup auto-activation of broker session deferred: %s", exc)

    instrument_repo = InstrumentRepository(db_path=app_settings.instruments_db_path)
    instrument_svc = InstrumentService(repository=instrument_repo)
    await instrument_svc.initialize()

    market_svc = MarketDataService(event_bus=bus)
    await market_svc.initialize()
    if app_settings.market_data_backend == MarketDataBackend.SIMULATED:
        await market_svc.start_simulated_feed(interval_sec=1.0)

    historical_repo = HistoricalRepository(db_path=app_settings.historical_db_path)
    historical_svc = HistoricalService(repository=historical_repo)
    await historical_svc.initialize()

    option_chain_svc = OptionChainService(
        instrument_service=instrument_svc,
        market_data_service=market_svc,
    )

    oms_repo = OMSRepository(db_path=app_settings.oms_db_path)
    oms_svc = OMSService(repository=oms_repo, event_bus=bus)
    await oms_svc.initialize()
    await oms_svc.start_outbox_worker(poll_interval_sec=0.2)

    live_gate = LiveTradingGate(settings=app_settings, event_bus=bus)

    risk_repo = RiskRepository(db_path=app_settings.risk_db_path)
    risk_svc = RiskService(
        repository=risk_repo,
        event_bus=bus,
        live_gate=live_gate,
        max_order_qty=1800,
    )
    await risk_svc.initialize()

    exec_svc = ExecutionService(
        broker_gateway=gateway_svc,
        oms_service=oms_svc,
        live_gate=live_gate,
        event_bus=bus,
    )
    await exec_svc.initialize()

    portfolio_repo = PortfolioRepository(db_path=app_settings.portfolio_db_path)
    portfolio_svc = PortfolioService(
        repository=portfolio_repo, market_data_service=market_svc, event_bus=bus
    )
    await portfolio_svc.initialize()

    strategy_repo = StrategyRepository(db_path=app_settings.strategy_db_path)
    strategy_svc = StrategyService(repository=strategy_repo, oms_service=oms_svc, event_bus=bus)
    await strategy_svc.initialize()

    audit_repo = AuditRepository(db_path=app_settings.audit_db_path)
    audit_svc = AuditService(repository=audit_repo, event_bus=bus)
    await audit_svc.initialize()

    auth_repo = AuthRepository(db_path=app_settings.auth_db_path)
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
