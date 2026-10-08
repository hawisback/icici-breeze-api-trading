"""Read-only AI-facing market context endpoints.

These endpoints expose evidence and objective calculations. They intentionally do
not emit trading recommendations, directional labels, setup scores, confidence
scores, or pre-ranked option contracts.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from services.ai_context.service import AIContextService
from services.api_gateway.service_container import get_services

router = APIRouter(prefix="/api/v1/ai", tags=["AI Market Context"])


def _context_service() -> AIContextService:
    services = get_services()
    return AIContextService(
        market_data_service=services.market_svc,
        historical_service=services.historical_svc,
        option_chain_service=services.option_chain_svc,
        portfolio_service=services.portfolio_svc,
        broker_gateway=services.gateway_svc,
        broker_session_service=services.session_svc,
        risk_service=services.risk_svc,
    )


@router.get("/nifty/snapshot")
async def get_nifty_ai_snapshot():
    """Compact starting context for each scheduled AI reasoning cycle."""
    return await _context_service().get_snapshot()


@router.get("/nifty/candles")
async def get_nifty_ai_candles(
    interval: str = Query(default="5m", pattern=r"^(1m|5m|15m)$"),
    limit: int = Query(default=100, ge=20, le=500),
):
    """Completed real-market candles for AI drill-down."""
    return await _context_service().get_candles(
        interval=interval,
        limit=limit,
    )


@router.get("/nifty/technicals")
async def get_nifty_ai_technicals(
    interval: str = Query(default="5m", pattern=r"^(1m|5m|15m)$"),
    limit: int = Query(default=200, ge=50, le=500),
):
    """Objective RSI, MACD, EMA, ATR, VWAP and return measurements."""
    return await _context_service().get_technicals(
        interval=interval,
        limit=limit,
    )


@router.get("/nifty/options")
async def get_nifty_ai_options(
    expiry: date | None = Query(default=None),
    strike_window: int = Query(default=10, ge=0, le=30),
):
    """Real option-chain evidence around ATM without contract ranking."""
    return await _context_service().get_options(
        underlying="NIFTY",
        expiry=expiry.isoformat() if expiry else None,
        strike_window=strike_window,
    )


@router.get("/account/context")
async def get_ai_account_context():
    """Portfolio, live broker account and safety state for AI risk awareness."""
    return await _context_service().get_account_context()


@router.get("/data-quality")
async def get_ai_data_quality():
    """Explicit feed/source freshness so AI can reject stale evidence."""
    return await _context_service().get_data_quality()
