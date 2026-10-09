"""AI-facing read-only context and controlled trade-intent endpoints.

These endpoints expose evidence and objective calculations. They intentionally do
not emit trading recommendations, directional labels, setup scores, confidence
scores, or pre-ranked option contracts.
"""

from __future__ import annotations

from datetime import date
import asyncio
from time import monotonic
from urllib.parse import urlencode

from libs.config import update_env_variable

from pydantic import BaseModel, ConfigDict, Field
from services.ai_context.trading import AITradeError
from services.api_gateway.ai_local_access import require_local_ai_client

from fastapi import APIRouter, Depends, HTTPException, Query

from services.ai_context.service import AIContextService
from services.api_gateway.service_container import get_services

_index_refresh_lock = asyncio.Lock()
_last_index_refresh = 0.0


async def _refresh_ai_only_index() -> None:
    """Refresh the real Kite index on demand, not via a global polling worker."""
    global _last_index_refresh
    services = get_services()
    if not getattr(services.settings, "ai_only_mode", False):
        return
    if monotonic() - _last_index_refresh < 2.0:
        return
    async with _index_refresh_lock:
        if monotonic() - _last_index_refresh < 2.0:
            return
        _last_index_refresh = monotonic()
        await services.market_svc.sync_quotes_from_broker()


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
        ai_only_mode=getattr(services.settings, "ai_only_mode", False),
    )



class AIBrokerLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: str = Field(pattern=r"^(kite|breeze)$")


class AIBrokerTokenRequest(AIBrokerLoginRequest):
    """Paste the broker's daily request_token (Kite) / apisession (Breeze)."""

    token: str = Field(min_length=5, max_length=4096)


def _broker_credentials(services, broker: str) -> tuple[str, str]:
    settings = services.settings
    raw_key = settings.kite_api_key if broker == "kite" else settings.breeze_api_key
    raw_secret = settings.kite_api_secret if broker == "kite" else settings.breeze_secret_key
    key = raw_key.get_secret_value() if raw_key else ""
    secret = raw_secret.get_secret_value() if raw_secret else ""
    if not key or not secret or key.startswith("your_") or secret.startswith("your_"):
        raise HTTPException(
            status_code=400,
            detail=f"BROKER_CREDENTIALS_NOT_CONFIGURED: configure {broker.upper()} keys in .env",
        )
    return key, secret


@router.get("/broker/sessions")
async def get_ai_broker_sessions():
    """Read-only daily Kite/Breeze connection status; do not expose tokens."""
    services = get_services()
    statuses = await services.session_svc.get_all_session_statuses()
    def public_status(broker: str) -> dict:
        setting = services.settings
        api_key = setting.kite_api_key if broker == "kite" else setting.breeze_api_key
        secret = setting.kite_api_secret if broker == "kite" else setting.breeze_secret_key
        key = api_key.get_secret_value() if api_key else ""
        sec = secret.get_secret_value() if secret else ""
        status = statuses.get(broker) or {}
        return {
            "broker": broker,
            "configured": bool(key and sec and not key.startswith("your_") and not sec.startswith("your_")),
            "connected": bool(status.get("connected")),
            "status": status.get("status", "DISCONNECTED"),
            "expires_at": status.get("expires_at"),
            "message": status.get("message"),
        }
    return {
        "kite": public_status("kite"),
        "breeze": public_status("breeze"),
        "market_data_broker": "kite",
        "trading_mode": services.settings.ai_trade_mode.value,
    }


@router.post("/broker/session/login-url")
async def issue_ai_broker_login_url(req: AIBrokerLoginRequest):
    """Start existing official broker redirect flow without platform-user login."""
    services = get_services()
    _broker_credentials(services, req.broker)
    challenge = services.session_svc.issue_login_challenge(
        initiated_by="LOCAL_AI_DASHBOARD",
        broker_backend=req.broker,
    )
    # /start sets the HttpOnly state cookie and redirects to the official
    # broker login page. The existing callback exchanges/persists the token.
    return {
        "broker": req.broker,
        "login_url": "http://127.0.0.1:8000/api/v1/broker/session/start?" + urlencode(
            {"broker": req.broker, "state": challenge["state"]}
        ),
        "expires_at": challenge["expires_at"],
    }


@router.post("/broker/session/activate")
async def activate_ai_broker_session(req: AIBrokerTokenRequest):
    """Local manual fallback for daily Kite request token or Breeze apisession."""
    services = get_services()
    api_key, secret_key = _broker_credentials(services, req.broker)
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="EMPTY_BROKER_TOKEN")
    try:
        result = await services.session_svc.activate_session(
            api_key=api_key,
            secret_key=secret_key,
            session_token=token,
            account_id="ZERODHA_PRIMARY" if req.broker == "kite" else "ICICI_PRIMARY",
            broker_backend=req.broker,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"BROKER_SESSION_ACTIVATION_FAILED:{type(exc).__name__}"
        ) from exc
    if not result.get("connected"):
        raise HTTPException(
            status_code=400, detail=result.get("status", "AUTHENTICATION_FAILED")
        )
    # Kite request tokens are one-time; persist exchanged daily access token.
    # Do not return tokens in HTTP responses or browser storage.
    env_key = "KITE_ACCESS_TOKEN" if req.broker == "kite" else "BREEZE_SESSION_TOKEN"
    persist_token = (
        getattr(services.gateway_svc.kite_adapter, "access_token", "")
        if req.broker == "kite" else token
    )
    persisted = False
    if persist_token:
        persisted = update_env_variable(key=env_key, value=persist_token)
    return {
        "broker": req.broker,
        "status": "CONNECTED",
        "connected": True,
        "persisted_for_restart": bool(persisted),
        "message": (
            "Broker session connected. Restart persistence succeeded."
            if persisted else "Broker session connected; .env token persistence unavailable."
        ),
    }


@router.get("/nifty/snapshot")
async def get_nifty_ai_snapshot():
    """Validated entry context, with blockers; never grants order authority."""
    await _refresh_ai_only_index()
    return await _context_service().get_snapshot()


@router.get("/nifty/candles")
async def get_nifty_ai_candles(
    interval: str = Query(default="5m", pattern=r"^(1m|5m|15m)$"),
    limit: int = Query(default=100, ge=20, le=500),
):
    """Completed real-market candles for AI drill-down."""
    await _refresh_ai_only_index()
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
    await _refresh_ai_only_index()
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


@router.get("/heavyweights")
async def get_ai_heavyweights():
    """One cached, timestamped batch of five NSE equities via Kite."""
    return await _context_service().get_heavyweights()


@router.get("/account/context")
async def get_ai_account_context():
    """Portfolio, live broker account and safety state for AI risk awareness."""
    return await _context_service().get_account_context()


@router.get("/data-quality")
async def get_ai_data_quality():
    """Explicit exchange and execution-feed freshness for AI gating."""
    await _refresh_ai_only_index()
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

