"""AI-facing read-only context and controlled trade-intent endpoints.

These endpoints expose evidence and objective calculations. They intentionally do
not emit trading recommendations, directional labels, setup scores, confidence
scores, or pre-ranked option contracts.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from services.ai_context.trading import AITradeError
from services.api_gateway.ai_local_access import require_local_ai_client

from fastapi import APIRouter, Depends, HTTPException, Query

from services.ai_context.service import AIContextService
from services.api_gateway.service_container import get_services

router = APIRouter(
    prefix="/api/v1/ai",
    tags=["AI Market Context"],
    dependencies=[Depends(require_local_ai_client)],  # no token; local clients only
)


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
        order_management_service=services.oms_svc,
    )


@router.get("/nifty/snapshot")
async def get_nifty_ai_snapshot():
    """Validated entry context, with blockers; never grants order authority."""
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
    """Completed-bar RSI/MACD histories, EMA, ATR, VWAP and returns."""
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
    """Explicit exchange and execution-feed freshness for AI gating."""
    return await _context_service().get_data_quality()


class AISignalIntent(BaseModel):
    """Trading mode, prices, and risk policy are deliberately not caller inputs."""

    model_config = ConfigDict(extra="forbid")
    signal_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    instrument_id: str = Field(min_length=12, max_length=140)
    quantity: int = Field(gt=0, le=1800)


@router.post("/trades", status_code=201)
async def submit_ai_trade(
    req: AISignalIntent,
):
    """Submit an idempotent signal ID; backend decides execution and all exits."""
    try:
        return await get_services().ai_trade_svc.submit(
            signal_id=req.signal_id,
            instrument_id=req.instrument_id,
            quantity=req.quantity,
        )
    except AITradeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("/trades")
async def list_ai_trades(
    limit: int = Query(default=25, ge=1, le=100),
):
    return await get_services().ai_trade_svc.list(limit=limit)


@router.get("/trades/{trade_id}")
async def get_ai_trade(
    trade_id: str,
):
    trade = await get_services().ai_trade_svc.get(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="TRADE_NOT_FOUND")
    return trade

