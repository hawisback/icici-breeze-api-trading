"""AI-facing read-only context and controlled trade-intent endpoints.

These endpoints expose evidence and objective calculations. They intentionally do
not emit trading recommendations, directional labels, setup scores, confidence
scores, or pre-ranked option contracts.
"""

from __future__ import annotations

from datetime import date
import hmac
import asyncio
from time import monotonic
from urllib.parse import urlencode

from libs.config import update_env_variable

from pydantic import BaseModel, ConfigDict, Field
from services.ai_context.trading import AITradeError
from services.api_gateway.ai_local_access import require_local_ai_client

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from libs.contracts.models import TradingMode

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
        "execution_mode": services.ai_execution_svc.mode.value,
        "execution_enabled": services.settings.ai_execution_enabled,
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
    """Legacy autonomous PAPER engine; disabled when external execution API is active."""
    if get_services().settings.ai_execution_enabled:
        raise HTTPException(status_code=409, detail="USE_AI_EXECUTION_API")
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



# ---------------------------------------------------------------------------
# Independent AI execution API (no strategy scheduler, no OMS, no auto stops).
# A mode-switching AI MUST present a separate shared secret on every command.
# Local peer filtering from the parent router still applies on every route.
# ---------------------------------------------------------------------------

async def require_ai_execution_key(
    token: str | None = Header(default=None, alias="X-AI-Execution-Key"),
) -> None:
    settings = get_services().settings
    if not settings.ai_execution_enabled:
        raise HTTPException(status_code=403, detail="AI_EXECUTION_DISABLED")
    expected = (settings.ai_execution_api_key.get_secret_value()
                if settings.ai_execution_api_key else "")
    if not token or not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="INVALID_AI_EXECUTION_KEY")


class AIExecutionModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: TradingMode


class AIExecutionEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    instrument_id: str = Field(min_length=12, max_length=140)
    quantity: int = Field(gt=0, le=1800)
    limit_price: float = Field(gt=0)


class AIExecutionStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    entry_order_id: str = Field(min_length=16, max_length=64)
    trigger_price: float = Field(gt=0)
    limit_price: float = Field(gt=0)


class AIExecutionStopModifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_price: float = Field(gt=0)
    limit_price: float = Field(gt=0)


class AIExecutionExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    entry_order_id: str = Field(min_length=16, max_length=64)
    limit_price: float = Field(gt=0)


def _execution_api_error(exc: AITradeError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


@router.get("/execution/status")
async def ai_execution_status():
    """Local read-only effective mode/capability. LIVE mode resets on restart."""
    return await get_services().ai_execution_svc.status()


@router.post("/execution/mode", dependencies=[Depends(require_ai_execution_key)])
async def set_ai_execution_mode(req: AIExecutionModeRequest):
    try:
        return await get_services().ai_execution_svc.set_mode(req.mode)
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.get("/execution/monitor")
async def ai_execution_monitor():
    """Local workstation read-only orders and exposure for the UI."""
    svc = get_services().ai_execution_svc
    status = await svc.status()
    if not status["execution_enabled"]:
        return {"status": status, "orders": [], "positions": []}
    try:
        return {"status": status,
                "orders": await svc.list_orders(),
                "positions": (await svc.positions())["positions"]}
    except AITradeError as exc:
        return {"status": status, "orders": await svc.list_orders(),
                "positions": [], "positions_error": exc.code}


@router.get("/execution/orders", dependencies=[Depends(require_ai_execution_key)])
async def list_ai_execution_orders():
    try:
        return await get_services().ai_execution_svc.list_orders()
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.get("/execution/orders/{order_id}", dependencies=[Depends(require_ai_execution_key)])
async def get_ai_execution_order(order_id: str):
    try:
        return await get_services().ai_execution_svc.get_order(order_id)
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.post("/execution/orders", status_code=201, dependencies=[Depends(require_ai_execution_key)])
async def place_ai_execution_entry(req: AIExecutionEntryRequest):
    try:
        return await get_services().ai_execution_svc.entry(
            request_id=req.request_id, instrument_id=req.instrument_id,
            quantity=req.quantity, limit_price=req.limit_price,
        )
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.get("/execution/positions", dependencies=[Depends(require_ai_execution_key)])
async def get_ai_execution_positions():
    try:
        return await get_services().ai_execution_svc.positions()
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.post("/execution/stops", status_code=201, dependencies=[Depends(require_ai_execution_key)])
async def place_ai_execution_stop(req: AIExecutionStopRequest):
    try:
        return await get_services().ai_execution_svc.place_stop(
            request_id=req.request_id, entry_order_id=req.entry_order_id,
            trigger_price=req.trigger_price, limit_price=req.limit_price,
        )
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.patch("/execution/stops/{stop_order_id}", dependencies=[Depends(require_ai_execution_key)])
async def modify_ai_execution_stop(stop_order_id: str, req: AIExecutionStopModifyRequest):
    try:
        return await get_services().ai_execution_svc.modify_stop(
            stop_order_id=stop_order_id, trigger_price=req.trigger_price,
            limit_price=req.limit_price,
        )
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.delete("/execution/stops/{stop_order_id}", dependencies=[Depends(require_ai_execution_key)])
async def cancel_ai_execution_stop(stop_order_id: str):
    try:
        return await get_services().ai_execution_svc.cancel_stop(stop_order_id)
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc


@router.post("/execution/exit", status_code=201, dependencies=[Depends(require_ai_execution_key)])
async def exit_ai_execution_position(req: AIExecutionExitRequest):
    try:
        return await get_services().ai_execution_svc.exit(
            request_id=req.request_id, entry_order_id=req.entry_order_id,
            limit_price=req.limit_price,
        )
    except AITradeError as exc:
        raise _execution_api_error(exc) from exc
