"""FastAPI API Gateway and WebSocket Multiplexer.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from services.api_gateway.error_handlers import register_error_handlers
from services.api_gateway.idempotency import execute_idempotent_command
from services.api_gateway.middleware import (
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    StrictOriginMiddleware,
)
from services.api_gateway.rate_limiter import RateLimiter

from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    OrderType,
    ProductType,
    SystemMode,
    TimeInForce,
    TradingMode,
    UserPrincipal,
    UserRole,
    utc_now,
)
from libs.events.bus import EventEnvelope, Topics
from libs.config import get_platform_settings, update_env_variable
from libs.observability.logger import setup_logging
from services.strategy.models import (
    AutoTradingConfig,
    AutoTradingMode,
    HistoricalReplaySource,
    OptionType,
    SimulationRequest,
    StrategyName,
    ThresholdOverrides,
    TradeDirection,
)
from services.api_gateway.dependencies import get_current_user, require_roles
from services.api_gateway.service_container import (
    ServiceContainer,
    get_services,
    initialize_services,
)
from services.broker_gateway.presentation import internal_router, set_clean_broker_service


setup_logging(level=logging.INFO)
logger = logging.getLogger("api-gateway")


# ==============================================================================
# WebSocket Connection Manager
# ==============================================================================


class WebSocketManager:
    """Manages active browser connections and broadcasts multiplexed live streams."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info("WebSocket client registered. Active: %d", len(self.active_connections))

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await self.register(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info("WebSocket client disconnected. Active: %d", len(self.active_connections))

    async def broadcast(self, message_type: str, data: Any) -> None:
        """Send formatted message to all connected clients."""
        payload = json.dumps(
            {
                "type": message_type,
                "data": data,
                "timestamp": utc_now().isoformat(),
            },
            default=str,
        )
        async with self._lock:
            disconnected = []
            for ws in self.active_connections:
                try:
                    await ws.send_text(payload)
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                if ws in self.active_connections:
                    self.active_connections.remove(ws)


ws_manager = WebSocketManager()


# ==============================================================================
# Lifespan
# ==============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up API Gateway & initializing services...")
    container = await initialize_services()
    set_clean_broker_service(container.gateway_svc.clean_breeze_service)

    # Wire event bus to websocket broadcasting
    async def on_quote_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("QUOTE", env.payload)

    async def on_candle_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("CANDLE", env.payload)

    async def on_order_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("ORDER", env.payload)

    async def on_position_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("POSITION", env.payload)

    async def on_pnl_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("PNL", env.payload)

    async def on_audit_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("ALERT", env.payload)

    async def on_strategy_event(env: EventEnvelope[Any]):
        await ws_manager.broadcast("STRATEGY", env.payload)

    await container.event_bus.subscribe(Topics.MARKET_QUOTE, on_quote_event)
    await container.event_bus.subscribe(Topics.MARKET_CANDLE, on_candle_event)
    await container.event_bus.subscribe(Topics.ORDER_STATE, on_order_event)
    await container.event_bus.subscribe(Topics.PORTFOLIO_POSITION, on_position_event)
    await container.event_bus.subscribe(Topics.PORTFOLIO_PNL, on_pnl_event)
    await container.event_bus.subscribe(Topics.AUDIT_EVENT, on_audit_event)
    await container.event_bus.subscribe(Topics.STRATEGY_SIGNAL, on_strategy_event)

    # Start optional port 80 listener for ICICI Direct default http://127.0.0.1/?apisession=... redirects
    port80_server = None
    try:
        async def handle_port80(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                line = await reader.readline()
                req_line = line.decode("utf-8", errors="ignore")
                import re
                match = re.search(r"apisession=([a-zA-Z0-9_-]+)", req_line)
                token = match.group(1) if match else None

                if token:
                    # Never activate or persist broker credentials on the raw
                    # port-80 listener. Forward the browser into the hardened
                    # callback, where the one-time login state is verified.
                    location = (
                        "http://127.0.0.1:8000/api/v1/broker/session/callback?"
                        + urlencode({"apisession": token})
                    )
                    raw_resp = (
                        "HTTP/1.1 302 Found\r\n"
                        f"Location: {location}\r\n"
                        "Cache-Control: no-store\r\n"
                        "Connection: close\r\n\r\n"
                    )
                else:
                    raw_resp = (
                        "HTTP/1.1 400 Bad Request\r\n"
                        "Content-Type: text/plain; charset=utf-8\r\n"
                        "Cache-Control: no-store\r\n"
                        "Connection: close\r\n\r\n"
                        "Missing apisession token."
                    )
                writer.write(raw_resp.encode("utf-8"))
                await writer.drain()
            except Exception as e:
                logger.warning("Error handling port 80 redirect: %s", e)
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        port80_server = await asyncio.start_server(handle_port80, "127.0.0.1", 80)
        logger.info("ICICI Direct 2FA redirect listener active on http://127.0.0.1:80/")
    except Exception as exc:
        logger.info("Port 80 redirect listener not started (optional): %s", exc)

    yield

    # Shutdown
    logger.info("Shutting down API Gateway...")
    if port80_server:
        try:
            port80_server.close()
            await port80_server.wait_closed()
        except Exception:
            pass
    await container.market_svc.stop_simulated_feed()
    await container.exec_svc.stop()
    await container.risk_svc.stop()
    await container.oms_svc.stop_outbox_worker()
    await container.event_bus.stop()


from libs.config.settings import get_platform_settings

platform_settings = get_platform_settings()
rate_limiter = RateLimiter(settings=platform_settings)

app = FastAPI(
    title="ICICI Direct Options Trading Platform API",
    version="2.0.0",
    description="Microservices API Gateway for options trading platform",
    lifespan=lifespan,
)

# Register structured exception handlers
register_error_handlers(app)

# Register internal broker gateway router
app.include_router(internal_router)

# Boundary protection middlewares (wrapping order: outermost wraps innermost)
app.add_middleware(RateLimitMiddleware, rate_limiter=rate_limiter, settings=platform_settings)
app.add_middleware(RequestSizeLimitMiddleware, settings=platform_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=platform_settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
        "Accept",
        "Origin",
    ],
    expose_headers=["Idempotency-Key", "Retry-After", "X-Request-ID", "X-Idempotency-Replay"],
)
app.add_middleware(StrictOriginMiddleware, settings=platform_settings)
app.add_middleware(SecurityHeadersMiddleware)


# ==============================================================================
# Request / Response Schemas
# ==============================================================================


class LoginRequest(BaseModel):
    api_key: str
    secret_key: str
    session_token: str = ""
    access_token: Optional[str] = None
    account_id: str = "ICICI_PRIMARY"
    broker: Optional[str] = Field(default=None, pattern=r"^(breeze|kite)$")


class OrderRequest(BaseModel):
    instrument_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.LIMIT
    quantity: int
    price: float
    trigger_price: Optional[float] = None
    product: ProductType = ProductType.OPTIONS
    time_in_force: TimeInForce = TimeInForce.DAY
    trading_mode: TradingMode = TradingMode.PAPER


class KillSwitchRequest(BaseModel):
    action: str = "BLOCK_ENTRIES"  # BLOCK_ENTRIES, EXIT_ONLY, HALT
    reason: str = "Operator manual intervention"


class SystemModeRequest(BaseModel):
    mode: SystemMode


class LiveGateChallengeRequest(BaseModel):
    operator_id: str = "OPERATOR"
    account_id: str = "ICICI_PRIMARY"
    duration_minutes: int = Field(default=30, ge=1, le=480)


class LiveGateConfirmRequest(BaseModel):
    challenge_id: str
    challenge_token: str
    operator_id: str = "OPERATOR"


class LiveGateRevokeRequest(BaseModel):
    operator_id: str = "OPERATOR"
    reason: str = "Operator manual revocation"


class UserLoginRequest(BaseModel):
    username: str
    password: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class StrategyModeRequest(BaseModel):
    mode: AutoTradingMode


class StrategyArmRequest(BaseModel):
    armed: bool


class StrategyAutoTradeRequest(BaseModel):
    enabled: bool


class StrategyKillSwitchRequest(BaseModel):
    active: bool


class StrategyExitRequest(BaseModel):
    reason: Optional[str] = "MANUAL_UI_EXIT"


class StrategyOverridesRequest(BaseModel):
    ema_slope_threshold: Optional[float] = Field(default=None, gt=0, le=1.0)
    max_option_premium_cap: Optional[float] = None
    min_option_premium_floor: Optional[float] = None
    adx_threshold: Optional[float] = None
    rvol_threshold: Optional[float] = None
    min_confirmation_score: Optional[int] = None
    strat_b_min_confirmation: Optional[int] = Field(default=None, ge=1, le=6)
    box_max_height_atr: Optional[float] = None
    bull_derivatives_score: Optional[float] = None
    bear_derivatives_score: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    bypass_entry_window: bool = False


class StrategyForceEntryRequest(BaseModel):
    strategy: StrategyName = StrategyName.TREND_PULLBACK
    direction: TradeDirection = TradeDirection.BULLISH
    option_type: Optional[OptionType] = None
    override_premium_cap: Optional[float] = None


# ==============================================================================
# REST Endpoints (/api/v1)
# ==============================================================================


@app.get("/api/v1/system/health")
@app.get("/api/v1/health")
@app.get("/health")
async def get_system_health():
    """Aggregated health check of all platform services."""
    services = get_services()
    session_status = await services.session_svc.get_session_status()
    feed_status = services.market_svc.get_feed_status()
    system_mode = await services.risk_svc.get_system_mode()
    strategy_status = await services.strategy_svc.get_status()

    broker_connected = bool(session_status.get("connected"))
    strategy_market = strategy_status.get("market_data", {})
    strategy_scheduler = strategy_status.get("scheduler", {})
    strategy_ready = bool(
        strategy_scheduler.get("running")
        and strategy_market.get("provider_active")
        and strategy_market.get("futures_candle_count", 0) > 0
    )

    return {
        "status": "HEALTHY" if broker_connected and strategy_ready else "DEGRADED",
        "timestamp": utc_now().isoformat(),
        "services": {
            "api_gateway": "ONLINE",
            "broker_session": "CONNECTED" if broker_connected else "DISCONNECTED",
            "market_feed": feed_status["status"],
            "strategy_scheduler": "RUNNING" if strategy_scheduler.get("running") else "STOPPED",
            "strategy_market_data": "READY" if strategy_ready else "NOT_READY",
            "strategy_market_data_reason": strategy_market.get("last_error"),
            "order_feed": "LIVE",
            "risk_engine": "ACTIVE",
            "oms": "ACTIVE",
            "portfolio": "ACTIVE",
            "system_mode": system_mode.value,
        },
        "config": services.settings.get_redacted_summary(),
    }


@app.post("/api/v1/auth/login")
async def login(req: UserLoginRequest):
    services = get_services()
    user, access_token, refresh_token, expires_in = await services.auth_svc.authenticate(
        username=req.username,
        password=req.password,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "user": user.model_dump(),
    }


@app.post("/api/v1/auth/refresh")
async def refresh_token(req: TokenRefreshRequest):
    services = get_services()
    user, new_access, new_refresh, expires_in = await services.auth_svc.refresh_tokens(
        raw_refresh_token=req.refresh_token,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked refresh token.",
        )
    return {
        "access_token": new_access,
        "token_type": "bearer",
        "expires_in": expires_in,
        "refresh_token": new_refresh,
        "user": user.model_dump(),
    }


@app.post("/api/v1/auth/logout")
async def logout(req: LogoutRequest):
    services = get_services()
    revoked = await services.auth_svc.logout(req.refresh_token)
    return {"status": "LOGGED_OUT", "revoked": revoked}


@app.get("/api/v1/auth/me")
async def get_my_profile(current_user: UserPrincipal = Depends(get_current_user)):
    return {
        "user": current_user.model_dump(),
        "permissions": {
            "can_trade": current_user.role in [UserRole.ADMIN, UserRole.TRADER],
            "can_operate_safety": current_user.role in [UserRole.ADMIN, UserRole.OPERATOR],
            "can_admin": current_user.role == UserRole.ADMIN,
        },
    }


@app.post("/api/v1/auth/ws-ticket")
async def create_ws_ticket(current_user: UserPrincipal = Depends(get_current_user)):
    services = get_services()
    ticket, expires_in = await services.auth_svc.create_ws_ticket(current_user)
    return {
        "ticket": ticket,
        "expires_in": expires_in,
        "ticket_type": "single_use",
    }



@app.get("/api/v1/broker/session/login-url")
@app.get("/api/v1/session/login-url")
async def get_session_login_url(
    broker: Optional[str] = Query(default=None, pattern=r"^(breeze|kite)$"),
    current_user: UserPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.OPERATOR)
    ),
):
    """Issue a one-time login flow for either configured broker."""
    settings = get_platform_settings()
    services = get_services()
    selected_broker = (
        broker
        or getattr(
            services.gateway_svc,
            "execution_broker_name",
            settings.broker_backend.value,
        )
    )
    challenge = services.session_svc.issue_login_challenge(
        initiated_by=current_user.user_id,
        broker_backend=selected_broker,
    )
    api_key_secret = (
        settings.kite_api_key
        if selected_broker == "kite"
        else settings.breeze_api_key
    )
    api_key = api_key_secret.get_secret_value() if api_key_secret else ""
    start_url = (
        "http://127.0.0.1:8000/api/v1/broker/session/start?"
        + urlencode(
            {
                "state": challenge["state"],
                "broker": selected_broker,
            }
        )
    )
    return {
        "login_url": start_url,
        "api_key": api_key,
        "callback_state": challenge["state"],
        "state_expires_at": challenge["expires_at"],
        "redirect_url_hint": "http://127.0.0.1:8000/api/v1/broker/session/callback",
        "broker": selected_broker,
        "instructions": (
            "Open this URL in the broker login flow. The callback is accepted "
            "only for this short-lived, one-time authenticated login state."
        ),
    }


@app.get("/api/v1/broker/session/start")
@app.get("/api/v1/session/start")
async def start_broker_session_login(
    state: str = Query(..., min_length=20),
    broker: Optional[str] = Query(default=None, pattern=r"^(breeze|kite)$"),
):
    """Bootstrap broker-specific correlation, then redirect to the official broker."""
    services = get_services()
    selected_broker = services.session_svc._normalize_broker(broker)
    if not services.session_svc.validate_login_challenge(
        state,
        broker_backend=selected_broker,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Broker login state is invalid, expired, or broker-mismatched.",
        )

    login_url = services.session_svc.get_login_url(
        broker_backend=selected_broker,
    )
    if selected_broker == "kite":
        redirect_params = urlencode({"state": state})
        login_url += "&" + urlencode({"redirect_params": redirect_params})

    response = RedirectResponse(url=login_url, status_code=302)
    response.set_cookie(
        key="broker_login_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/v1/broker/session/status")
@app.get("/api/v1/session/status")
async def get_session_status(
    broker: Optional[str] = Query(default=None, pattern=r"^(breeze|kite)$"),
):
    services = get_services()
    if broker:
        return await services.session_svc.get_session_status(broker)
    execution = await services.session_svc.get_session_status()
    execution["sessions"] = await services.session_svc.get_all_session_statuses()
    execution["execution_broker"] = services.gateway_svc.execution_broker_name
    execution["frequent_data_broker"] = services.gateway_svc.frequent_data_broker_name
    execution["reference_data_broker"] = services.gateway_svc.reference_data_broker_name
    return execution


@app.post("/api/v1/broker/session/login")
@app.post("/api/v1/session/login")
async def session_login(
    req: LoginRequest,
    current_user: UserPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.OPERATOR)
    ),
):
    services = get_services()
    result = await services.session_svc.activate_session(
        api_key=req.api_key,
        secret_key=req.secret_key,
        session_token=req.session_token,
        access_token=req.access_token,
        account_id=req.account_id,
        broker_backend=req.broker,
    )
    return result


@app.get("/api/v1/broker/session/callback")
@app.get("/api/v1/session/callback")
@app.get("/callback")
async def broker_session_callback(
    request: Request,
    apisession: Optional[str] = None,
    session_token: Optional[str] = None,
    token: Optional[str] = None,
    request_token: Optional[str] = None,
    state: Optional[str] = None,
):
    """OAuth callback endpoint handling ICICI Direct 2FA redirect.

    Captures daily `apisession`, updates `.env` directly, synchronizes runtime configuration,
    and activates running broker sessions across all services.
    """
    settings = get_platform_settings()
    # The one-time challenge, not execution ownership, determines which broker
    # this callback is authenticating.
    raw_hint = (
        request.query_params.get("broker")
        or (
            "kite"
            if request_token
            else "breeze"
            if (apisession or session_token or token)
            else None
        )
    )
    is_kite = raw_hint == "kite"
    broker_display_name = "Kite" if is_kite else "ICICI Breeze"
    success_event_type = "KITE_SESSION_SUCCESS" if is_kite else "BREEZE_SESSION_SUCCESS"
    raw_token = request_token if is_kite else (apisession or session_token or token)
    if not raw_token:
        # Check query parameters directly as fallback
        raw_token = (
            request.query_params.get("request_token")
            if is_kite
            else request.query_params.get("apisession") or request.query_params.get("session_token")
        )

    accept_header = request.headers.get("accept", "")
    wants_json = "application/json" in accept_header

    if not raw_token or not raw_token.strip():
        msg = "Missing broker session token in the callback query parameters."
        if wants_json:
            return JSONResponse(
                status_code=400,
                content={"status": "ERROR", "message": msg},
            )
        return HTMLResponse(
            status_code=400,
            content=f"""<!DOCTYPE html>
<html>
<head><title>Authentication Failed</title>
<style>
body {{ background: #020617; color: #f8fafc; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
.card {{ background: #0f172a; border: 1px solid #ef4444; border-radius: 12px; padding: 32px; max-width: 480px; text-align: center; }}
h1 {{ color: #f87171; margin-top: 0; }}
p {{ color: #94a3b8; font-size: 14px; }}
a {{ color: #38bdf8; text-decoration: none; }}
</style>
</head>
<body>
<div class="card">
  <h1>Authentication Incomplete</h1>
  <p>{msg}</p>
  <p>Please make sure you complete login from the official ICICI Direct link.</p>
</div>
</body>
</html>""",
        )

    token_clean = raw_token.strip()
    masked = token_clean[:4] + "..." + token_clean[-4:] if len(token_clean) > 8 else "***"

    # Callback requests cannot carry the operator bearer token, so require a
    # short-lived state that was issued only after RBAC-authenticated login
    # initiation. Browser redirects may supply it via query (Kite) or the
    # HttpOnly bootstrap cookie (Breeze/local redirect).
    cookie_state = request.cookies.get("broker_login_state")
    query_state = state or request.query_params.get("state")
    if query_state and cookie_state and query_state != cookie_state:
        correlation_state = None
    else:
        correlation_state = query_state or cookie_state

    challenge = (
        get_services().session_svc.consume_login_challenge(correlation_state)
        if correlation_state
        else None
    )
    if challenge is None:
        msg = "Broker callback rejected: login state is missing, expired, or already used."
        if wants_json:
            return JSONResponse(
                status_code=403,
                content={"status": "ERROR", "message": msg},
            )
        return HTMLResponse(
            status_code=403,
            content=f"""<!DOCTYPE html>
<html><head><title>Authentication Rejected</title></head>
<body><h1>Authentication Rejected</h1><p>{msg}</p></body></html>""",
        )

    challenge_broker = str(
        challenge.get("broker_backend")
        or raw_hint
        or settings.broker_backend.value
    ).lower()
    is_kite = challenge_broker == "kite"
    broker_display_name = "Kite" if is_kite else "ICICI Breeze"
    success_event_type = "KITE_SESSION_SUCCESS" if is_kite else "BREEZE_SESSION_SUCCESS"

    # 1. Update .env file directly and sync runtime PlatformSettings
    # Breeze can persist its callback token. Kite request tokens are one-time and
    # short-lived, so the exchanged access token is persisted after activation.
    env_updated = False if is_kite else update_env_variable(
        key="BREEZE_SESSION_TOKEN",
        value=token_clean,
    )

    # 2. Activate in-memory session and broker gateway
    services = get_services()
    api_secret = settings.kite_api_key if is_kite else settings.breeze_api_key
    secret_secret = settings.kite_api_secret if is_kite else settings.breeze_secret_key
    api_key = api_secret.get_secret_value() if api_secret else ""
    secret_key = secret_secret.get_secret_value() if secret_secret else ""

    session_result = {}
    if api_key and secret_key:
        try:
            session_result = await services.session_svc.activate_session(
                api_key=api_key,
                secret_key=secret_key,
                session_token=token_clean,
                account_id="ZERODHA_PRIMARY" if is_kite else "ICICI_PRIMARY",
                broker_backend=challenge_broker,
            )
        except Exception as exc:
            logger.warning("Session service activation produced warning: %s", exc)
            session_result = {"status": "ACTIVATING_DEFERRED", "error": str(exc)}

    if is_kite and session_result.get("connected"):
        kite_access_token = getattr(services.gateway_svc.kite_adapter, "access_token", "")
        if kite_access_token:
            env_updated = update_env_variable(
                key="KITE_ACCESS_TOKEN",
                value=kite_access_token,
            )

    if session_result.get("status") == "AUTHENTICATION_FAILED":
        err_msg = session_result.get("message", "Authentication rejected by ICICI Direct. Session key is expired or invalid.")
        if wants_json:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "ERROR",
                    "message": err_msg,
                    "token_masked": masked,
                    "session": session_result,
                },
            )

    if wants_json:
        return JSONResponse(
            content={
                "status": "SUCCESS",
                "message": f"{broker_display_name} session token captured, persisted to .env, and activated.",
                "token_masked": masked,
                "env_updated": env_updated,
                "session": session_result,
            }
        )

    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Breeze Session Authenticated</title>
  <style>
    body {{
      background-color: #020617;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }}
    .card {{
      background-color: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 12px;
      padding: 36px;
      max-width: 480px;
      text-align: center;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .icon {{
      width: 56px;
      height: 56px;
      background-color: #064e3b;
      color: #34d399;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      margin-bottom: 20px;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 600;
      margin: 0 0 8px 0;
      color: #f1f5f9;
    }}
    p {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 20px 0;
    }}
    .badge {{
      display: inline-block;
      background-color: #022c22;
      color: #6ee7b7;
      border: 1px solid #065f46;
      padding: 6px 14px;
      border-radius: 6px;
      font-family: monospace;
      font-size: 13px;
      margin-bottom: 24px;
    }}
    .btn {{
      background-color: #0284c7;
      color: white;
      border: none;
      padding: 10px 20px;
      border-radius: 6px;
      font-weight: 500;
      cursor: pointer;
      font-size: 14px;
    }}
    .btn:hover {{
      background-color: #0369a1;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">&#10003;</div>
    <h1>{broker_display_name} Session Authenticated</h1>
    <p>Session token successfully captured, saved to <code>.env</code>, and activated across platform services.</p>
    <div class="badge">TOKEN: {masked}</div>
    <div>
      <button class="btn" onclick="window.close()">Close Window</button>
    </div>
  </div>
  <script>
    if (window.opener) {{
      window.opener.postMessage({{
        type: '{success_event_type}',
        token: '{masked}',
        status: 'CONNECTED'
      }}, '*');
      setTimeout(function() {{
        window.close();
      }}, 2200);
    }}
  </script>
</body>
</html>"""
    )


@app.get("/api/v1/account/funds")
async def get_account_funds(mode: Optional[TradingMode] = None):
    services = get_services()
    session_status = await services.session_svc.get_session_status()
    target_mode = mode
    if target_mode is None:
        target_mode = TradingMode.LIVE if session_status.get("connected") else TradingMode.PAPER

    try:
        funds = await services.gateway_svc.get_funds(mode=target_mode)
        return funds.model_dump()
    except Exception as exc:
        logger.warning("Error querying funds in %s mode: %s", target_mode, exc)
        paper_funds = await services.gateway_svc.get_funds(mode=TradingMode.PAPER)
        return paper_funds.model_dump()


@app.get("/api/v1/instruments/search")
async def search_instruments(query: str = "", underlying: Optional[str] = None):
    services = get_services()
    instruments = await services.instrument_svc.search(query=query, underlying=underlying)
    return [i.model_dump() for i in instruments]


@app.get("/api/v1/instruments/expiries")
async def get_expiries(underlying: str = "NIFTY"):
    services = get_services()
    expiries = await services.instrument_svc.get_expiries(underlying=underlying)
    return {"underlying": underlying, "expiries": expiries}


@app.get("/api/v1/market/quote/{instrument_id}")
async def get_quote(instrument_id: str):
    services = get_services()
    quote = services.market_svc.get_latest_quote(instrument_id)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote.model_dump()


@app.get("/api/v1/market/quotes")
async def get_all_quotes():
    services = get_services()
    quotes = services.market_svc.get_all_quotes()
    return [q.model_dump() for q in quotes]


@app.get("/api/v1/market/candles")
async def get_candles(
    instrument_id: str = "INST-NIFTY-INDEX",
    interval: str = "5m",
    limit: int = 100,
):
    services = get_services()
    candles = await services.historical_svc.get_candles(
        instrument_id=instrument_id,
        interval=interval,
        limit=limit,
    )
    return [c.model_dump() for c in candles]


@app.get("/api/v1/options/chain")
async def get_option_chain(underlying: str = "NIFTY", expiry: Optional[str] = None):
    services = get_services()
    return await services.option_chain_svc.get_chain(underlying=underlying, expiry=expiry)


@app.get("/api/v1/orders")
async def list_orders(limit: int = 100):
    services = get_services()
    orders = await services.oms_svc.list_orders(limit=limit)
    return [o.model_dump() for o in orders]


@app.post("/api/v1/orders")
async def create_order(
    req: OrderRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.TRADER)),
):
    services = get_services()

    async def _execute():
        intent = OrderIntent(
            instrument_id=req.instrument_id,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            quantity=req.quantity,
            price=req.price,
            trigger_price=req.trigger_price,
            product=req.product,
            time_in_force=req.time_in_force,
            trading_mode=req.trading_mode,
        )
        order = await services.oms_svc.create_order_intent(intent)
        return order.model_dump()

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path="/api/v1/orders",
        payload_dict=req.model_dump(),
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.post("/api/v1/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.TRADER)),
):
    services = get_services()

    async def _execute():
        order = await services.oms_svc.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.broker_order_id:
            resp = await services.gateway_svc.cancel_order(order.broker_order_id, mode=order.trading_mode)
            return resp.model_dump()
        return {"message": "Order cancel initiated"}

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path=f"/api/v1/orders/{order_id}/cancel",
        payload_dict={"order_id": order_id},
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.get("/api/v1/positions")
async def list_positions():
    services = get_services()
    positions = await services.portfolio_svc.get_positions()
    return [p.model_dump() for p in positions]


@app.get("/api/v1/pnl/summary")
async def get_pnl_summary():
    services = get_services()
    return await services.portfolio_svc.get_pnl_summary()


@app.get("/api/v1/risk/status")
async def get_risk_status():
    services = get_services()
    mode = await services.risk_svc.get_system_mode()
    return {"system_mode": mode.value}


@app.post("/api/v1/risk/kill-switch")
async def trigger_kill_switch(
    req: KillSwitchRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()

    async def _execute():
        event_id = await services.risk_svc.trigger_kill_switch(action=req.action, reason=req.reason)
        return {"event_id": event_id, "action": req.action, "status": "ACTIVATED"}

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path="/api/v1/risk/kill-switch",
        payload_dict=req.model_dump(),
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.post("/api/v1/risk/system-mode")
async def set_system_mode(
    req: SystemModeRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()

    async def _execute():
        await services.risk_svc.set_system_mode(req.mode)
        return {"system_mode": req.mode.value}

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path="/api/v1/risk/system-mode",
        payload_dict=req.model_dump(),
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.get("/api/v1/live-gate/status")
async def get_live_gate_status():
    services = get_services()
    return services.live_gate.get_status()


@app.get("/api/v1/live-preflight")
async def get_live_preflight(
    current_user: UserPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.OPERATOR)
    ),
):
    """Fail-closed readiness report for opening a server LIVE window."""
    services = get_services()
    settings = services.settings
    session = await services.session_svc.get_session_status()
    strategy = await services.strategy_svc.get_status()
    system_mode = await services.risk_svc.get_system_mode()
    live_gate = services.live_gate.get_status()
    orders = await services.oms_svc.list_orders(limit=500)

    nonterminal = {
        "CREATED",
        "VALIDATING",
        "APPROVED",
        "SUBMITTING",
        "SUBMISSION_UNKNOWN",
        "ACKNOWLEDGED",
        "OPEN",
        "PARTIALLY_FILLED",
    }
    unresolved_live_orders = [
        order
        for order in orders
        if order.trading_mode == TradingMode.LIVE
        and order.status.value in nonterminal
    ]

    broker_positions = []
    funds = None
    broker_error = None
    if session.get("connected"):
        try:
            broker_positions = await services.gateway_svc.get_positions(
                mode=TradingMode.LIVE
            )
            funds = await services.gateway_svc.get_funds(
                mode=TradingMode.LIVE
            )
        except Exception as exc:
            logger.exception("LIVE preflight broker verification failed")
            broker_error = type(exc).__name__

    open_broker_positions = [
        position for position in broker_positions if int(position.quantity) != 0
    ]
    reconciliation = await services.strategy_svc.build_live_reconciliation_report(
        broker_positions=broker_positions,
        orders=orders,
        broker_verified=bool(session.get("connected")) and broker_error is None,
        broker_error=(
            broker_error
            or (
                None
                if session.get("connected")
                else "BROKER_SESSION_NOT_CONNECTED"
            )
        ),
    )
    market = strategy.get("market_data") or {}
    scheduler = strategy.get("scheduler") or {}
    configured_bus = settings.event_bus_backend.value
    runtime_bus = (
        "memory"
        if type(services.event_bus).__name__ == "InMemoryEventBus"
        else type(services.event_bus).__name__
    )

    blockers: list[str] = []
    warnings: list[str] = []

    if not settings.live_trading_enabled:
        blockers.append("LIVE_TRADING_ENABLED_FALSE")
    expected_account_id = (
        "ZERODHA_PRIMARY"
        if settings.broker_backend.value == "kite"
        else "ICICI_PRIMARY"
    )
    if not settings.live_allowed_accounts:
        blockers.append("NO_LIVE_ALLOWED_ACCOUNT")
    elif expected_account_id not in settings.live_allowed_accounts:
        blockers.append("ACTIVE_BROKER_ACCOUNT_NOT_ALLOWLISTED")
    if not session.get("connected"):
        blockers.append("BROKER_SESSION_NOT_CONNECTED")
    if broker_error:
        blockers.append(f"BROKER_VERIFICATION_FAILED:{broker_error}")
    if settings.market_data_backend.value == "simulated":
        blockers.append("SIMULATED_MARKET_DATA")
    if not market.get("provider_active"):
        blockers.append("STRATEGY_MARKET_DATA_NOT_ACTIVE")
    if not market.get("execution_feed_healthy", False):
        feed_reasons = market.get("execution_feed_reasons") or [
            "UNKNOWN"
        ]
        blockers.append(
            "EXECUTION_FEED_UNHEALTHY:" + ";".join(feed_reasons)
        )
    if not scheduler.get("running"):
        blockers.append("STRATEGY_SCHEDULER_NOT_RUNNING")

    strategy_config = strategy.get("config", {}) or {}
    strategy_mode = str(strategy_config.get("mode") or "UNKNOWN")
    if strategy_mode != AutoTradingMode.LIVE.value:
        blockers.append(f"STRATEGY_MODE_NOT_LIVE:{strategy_mode}")
    if strategy_config.get("kill_switch"):
        blockers.append("STRATEGY_KILL_SWITCH_ACTIVE")

    execution_policy = strategy.get("execution_policy") or {}
    strategy_statuses = strategy.get("strategies") or {}
    enabled_live_strategies = [
        key
        for key, value in strategy_statuses.items()
        if bool(value.get("enabled"))
        and bool(value.get("live_trading_allowed"))
    ]
    if not enabled_live_strategies:
        blockers.append("NO_ENABLED_STRATEGY_LIVE_PROMOTED")

    strategy_a_enabled = bool(
        strategy.get("config", {})
        .get("tunables", {})
        .get("trend_pullback_enabled", False)
    )
    if (
        strategy_a_enabled
        and not market.get("strategy_a_option_execution_ready", False)
    ):
        reason = str(
            market.get("strategy_a_option_execution_reason")
            or "VERIFIED_OPTION_DELTA_UNAVAILABLE"
        )
        blockers.append(f"STRATEGY_A_OPTION_EXECUTION_BLOCKED:{reason}")
    if system_mode != SystemMode.NORMAL:
        blockers.append(f"RISK_MODE_{system_mode.value}")
    if unresolved_live_orders:
        blockers.append("UNRESOLVED_LIVE_ORDERS")
    for issue in reconciliation.get("issues", []):
        blockers.append(f"RECONCILIATION:{issue}")
    if funds is not None and float(funds.available_margin) <= 0:
        blockers.append("NO_AVAILABLE_BROKER_MARGIN")
    if configured_bus != runtime_bus:
        blockers.append(
            f"EVENT_BUS_RUNTIME_MISMATCH:{configured_bus}->{runtime_bus}"
        )
    elif runtime_bus == "memory":
        warnings.append(
            "EVENT_BUS_MEMORY_SINGLE_PROCESS; order path uses durable SQLite "
            "outboxes and broker reconciliation."
        )

    if strategy_config.get("system_armed"):
        blockers.append(
            "STRATEGY_MUST_BE_DISARMED_BEFORE_LIVE_AUTHORIZATION"
        )
    if strategy_config.get("auto_trade_enabled"):
        warnings.append("AUTO_TRADE_ALREADY_ENABLED")

    if blockers:
        readiness = "BLOCKED"
    elif live_gate.get("live_authorized"):
        readiness = "READY_FOR_LIVE"
    else:
        readiness = "READY_FOR_AUTHORIZATION"

    return {
        "readiness": readiness,
        "checked_at": utc_now().isoformat(),
        "checked_by": current_user.user_id,
        "blockers": blockers,
        "warnings": warnings,
        "broker": {
            "backend": settings.broker_backend.value,
            "connected": bool(session.get("connected")),
            "account_id": session.get("account_id"),
            "expires_at": session.get("expires_at"),
            "available_margin": (
                float(funds.available_margin) if funds is not None else None
            ),
            "open_positions": len(open_broker_positions),
        },
        "market_data": {
            "configured_backend": settings.market_data_backend.value,
            "strategy_provider": market.get("provider"),
            "provider_active": bool(market.get("provider_active")),
            "execution_feed_healthy": bool(
                market.get("execution_feed_healthy")
            ),
            "execution_feed_status": market.get(
                "execution_feed_status"
            ),
            "execution_feed_reasons": market.get(
                "execution_feed_reasons",
                [],
            ),
            "execution_feed_max_age_seconds": market.get(
                "execution_feed_max_age_seconds"
            ),
            "latest_spot_5m_candle_age_seconds": market.get(
                "latest_spot_5m_candle_age_seconds"
            ),
            "latest_futures_15m_candle_age_seconds": market.get(
                "latest_futures_15m_candle_age_seconds"
            ),
            "strategy_a_signal_data_fresh": bool(
                market.get("strategy_a_signal_data_fresh")
            ),
            "strategy_b_signal_data_fresh": bool(
                market.get("strategy_b_signal_data_fresh")
            ),
            "futures_candle_count": market.get("futures_candle_count", 0),
        },
        "reconciliation": reconciliation,
        "startup_reconciliation": strategy.get("startup_reconciliation"),
        "orders": {
            "unresolved_live_count": len(unresolved_live_orders),
            "unresolved_live": [
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "status": order.status.value,
                    "reduce_only": order.reduce_only,
                }
                for order in unresolved_live_orders
            ],
        },
        "risk": {
            "system_mode": system_mode.value,
            "max_order_notional": settings.live_max_order_notional,
            "max_open_positions": settings.live_max_open_positions,
        },
        "live_gate": live_gate,
        "strategy": {
            "scheduler_running": bool(scheduler.get("running")),
            "system_armed": bool(
                strategy.get("config", {}).get("system_armed")
            ),
            "auto_trade_enabled": bool(
                strategy.get("config", {}).get("auto_trade_enabled")
            ),
            "enabled_live_strategies": enabled_live_strategies,
            "execution_policy": execution_policy,
            "routing": {
                key: {
                    "execution_mode": value.get("execution_mode"),
                    "effective_call_mode": value.get("effective_call_mode"),
                    "effective_put_mode": value.get("effective_put_mode"),
                    "promotion_state": value.get("promotion_state"),
                    "live_block_reason": value.get("live_block_reason"),
                    "live_trading_allowed": value.get(
                        "live_trading_allowed",
                        False,
                    ),
                }
                for key, value in strategy_statuses.items()
            },
        },
        "event_bus": {
            "configured": configured_bus,
            "runtime": runtime_bus,
        },
        "protective_stop": {
            "supported": settings.broker_backend.value in {"breeze", "kite"},
            "order_type": "STOP_LIMIT",
        },
    }


@app.post("/api/v1/live-gate/challenge")
async def request_live_gate_challenge(
    req: LiveGateChallengeRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    preflight = await get_live_preflight(current_user)
    if preflight.get("readiness") != "READY_FOR_AUTHORIZATION":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "LIVE activation challenge blocked by preflight",
                "readiness": preflight.get("readiness"),
                "blockers": preflight.get("blockers", []),
            },
        )
    account_id = (
        "ZERODHA_PRIMARY"
        if services.settings.broker_backend.value == "kite"
        else "ICICI_PRIMARY"
    )
    try:
        return await services.live_gate.request_activation_challenge(
            operator_id=current_user.user_id,
            account_id=account_id,
            duration_minutes=req.duration_minutes,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@app.post("/api/v1/live-gate/confirm")
async def confirm_live_gate(
    req: LiveGateConfirmRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()

    async def _execute():
        preflight = await get_live_preflight(current_user)
        if preflight.get("readiness") != "READY_FOR_AUTHORIZATION":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "LIVE activation confirmation blocked because "
                        "preflight changed"
                    ),
                    "readiness": preflight.get("readiness"),
                    "blockers": preflight.get("blockers", []),
                },
            )
        confirmed = await services.live_gate.confirm_activation(
            challenge_id=req.challenge_id,
            challenge_token=req.challenge_token,
            operator_id=current_user.user_id,
        )
        if not confirmed:
            raise HTTPException(
                status_code=400,
                detail="LIVE activation failed: invalid or expired challenge token or operator mismatch.",
            )
        return {
            "status": "CONFIRMED",
            "message": "LIVE trading mode successfully activated.",
            "gate_status": services.live_gate.get_status(),
        }

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path="/api/v1/live-gate/confirm",
        payload_dict=req.model_dump(),
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.post("/api/v1/live-gate/revoke")
async def revoke_live_gate(
    req: LiveGateRevokeRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()

    async def _execute():
        await services.live_gate.revoke_live_mode(
            operator_id=current_user.user_id,
            reason=req.reason,
        )
        return {
            "status": "REVOKED",
            "message": "LIVE trading mode revoked immediately.",
            "gate_status": services.live_gate.get_status(),
        }

    return await execute_idempotent_command(
        idempotency_key=idempotency_key,
        user_id=current_user.user_id,
        method="POST",
        path="/api/v1/live-gate/revoke",
        payload_dict=req.model_dump(),
        execute_coroutine_fn=_execute,
        idempotency_repo=services.idempotency_repo,
        event_bus=services.event_bus,
        ttl_seconds=services.settings.idempotency_ttl_seconds,
    )


@app.get("/api/v1/strategies")
async def list_strategies():
    services = get_services()
    return await services.strategy_svc.list_instances()


@app.get("/api/v1/strategies/status")
async def get_strategy_status():
    services = get_services()
    return await services.strategy_svc.get_status()


@app.get("/api/v1/strategies/config")
async def get_strategy_config():
    services = get_services()
    cfg = await services.strategy_svc.get_config()
    return cfg.model_dump(mode="json")


@app.post("/api/v1/strategies/mode")
async def set_local_strategy_mode(
    req: StrategyModeRequest,
    request: Request,
):
    """Local workstation mode switch. It never arms the strategy system."""
    services = get_services()
    settings = services.settings
    client_host = (
        request.client.host.strip().lower()
        if request.client and request.client.host
        else ""
    )
    if not settings.local_single_user_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Unauthenticated strategy mode switching is available only "
                "when LOCAL_SINGLE_USER_MODE=true."
            ),
        )
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="LOCAL_SINGLE_USER_MODE accepts loopback requests only.",
        )
    if req.mode == AutoTradingMode.LIVE and not settings.live_trading_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Server LIVE capability is disabled by configuration.",
        )

    try:
        updated = await services.strategy_svc.set_execution_mode(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "status": "SUCCESS",
        "mode": updated.mode.value,
        "system_armed": updated.system_armed,
        "auto_trade_enabled": updated.auto_trade_enabled,
        "config": updated.model_dump(mode="json"),
    }


@app.post("/api/v1/strategies/config")
async def update_strategy_config(
    config_data: dict[str, Any],
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    try:
        cfg = AutoTradingConfig.model_validate(config_data)
        updated = await services.strategy_svc.update_config(cfg)
        return {"status": "SUCCESS", "config": updated.model_dump(mode="json")}
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.post("/api/v1/strategies/arm")
async def arm_strategy_system(
    req: StrategyArmRequest,
    current_user: UserPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.OPERATOR)
    ),
):
    services = get_services()
    current_config = await services.strategy_svc.get_config()
    if req.armed and current_config.mode == AutoTradingMode.LIVE:
        preflight = await get_live_preflight(current_user)
        if preflight.get("readiness") != "READY_FOR_LIVE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "LIVE strategy arming blocked by current preflight"
                    ),
                    "readiness": preflight.get("readiness"),
                    "blockers": preflight.get("blockers", []),
                },
            )
    updated = await services.strategy_svc.arm_system(req.armed)
    return {
        "status": "SUCCESS",
        "config": updated.model_dump(mode="json"),
    }


@app.post("/api/v1/strategies/auto-trade")
async def set_strategy_auto_trade(
    req: StrategyAutoTradeRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    updated = await services.strategy_svc.set_auto_trade(req.enabled)
    return {"status": "SUCCESS", "config": updated.model_dump(mode="json")}


@app.post("/api/v1/strategies/kill-switch")
async def toggle_strategy_kill_switch(
    req: StrategyKillSwitchRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    updated = await services.strategy_svc.toggle_kill_switch(req.active)
    return {"status": "SUCCESS", "config": updated.model_dump(mode="json")}


@app.post("/api/v1/strategies/evaluate-now")
async def evaluate_strategy_now(
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    result = await services.strategy_svc.evaluate_cycle()
    return {"status": "SUCCESS", "result": result}


@app.get("/api/v1/strategies/decision-log")
async def get_strategy_decision_log(limit: int = 100):
    services = get_services()
    return await services.strategy_svc.list_decision_logs(limit=limit)


@app.get("/api/v1/strategies/trades")
async def get_strategy_trades(limit: int = 50):
    services = get_services()
    return await services.strategy_svc.list_trades(limit=limit)


@app.get("/api/v1/strategies/eod-report")
async def get_strategy_eod_report(session_date: Optional[str] = Query(default=None)):
    services = get_services()
    return await services.strategy_svc.get_eod_report(session_date=session_date)


@app.post("/api/v1/strategies/trades/{trade_id}/exit")
async def exit_strategy_trade(
    trade_id: str,
    req: StrategyExitRequest = StrategyExitRequest(),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    exited = await services.strategy_svc.manual_exit_trade(trade_id, reason=req.reason or "MANUAL_UI_EXIT")
    if not exited:
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    return {"status": "SUCCESS", "trade": exited.model_dump(mode="json")}


@app.get("/api/v1/strategies/triggers/diagnostics")
async def get_strategy_trigger_diagnostics():
    services = get_services()
    diag = await services.strategy_svc.get_trigger_diagnostics()
    return diag.model_dump(mode="json")


@app.get("/api/v1/strategies/overrides")
async def get_strategy_overrides():
    services = get_services()
    overrides = services.strategy_svc.get_active_overrides()
    return overrides.model_dump(mode="json")


@app.post("/api/v1/strategies/overrides")
async def update_strategy_overrides(
    req: StrategyOverridesRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    overrides = ThresholdOverrides(**req.model_dump())
    updated = await services.strategy_svc.update_overrides(overrides)
    return {"status": "SUCCESS", "overrides": updated.model_dump(mode="json")}


@app.post("/api/v1/strategies/overrides/reset")
async def reset_strategy_overrides(
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    reset = await services.strategy_svc.reset_overrides()
    return {"status": "SUCCESS", "overrides": reset.model_dump(mode="json")}


@app.post("/api/v1/strategies/force-entry")
async def force_strategy_entry(
    req: StrategyForceEntryRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    res = await services.strategy_svc.force_entry(
        strategy=req.strategy,
        direction=req.direction,
        option_type=req.option_type,
        override_premium_cap=req.override_premium_cap,
    )
    return res


@app.post("/api/v1/strategies/simulate")
async def run_strategy_simulation(req: SimulationRequest):
    services = get_services()
    res = await services.strategy_svc.run_simulation(req)
    return res.model_dump(mode="json")


@app.get("/api/v1/strategies/simulate/available-dates")
async def get_simulation_available_dates(
    historical_source: HistoricalReplaySource = Query(default=HistoricalReplaySource.BREEZE),
):
    try:
        services = get_services()
        dates = await services.strategy_svc.get_available_simulation_dates(historical_source)
        return {"dates": dates}
    except Exception as exc:
        logger.exception("Failed to load available simulation dates")
        raise HTTPException(
            status_code=503,
            detail="Historical simulation dates are temporarily unavailable.",
        ) from exc


@app.get("/api/v1/audit/logs")
async def get_audit_logs(limit: int = 100):
    services = get_services()
    return await services.audit_svc.get_recent_logs(limit=limit)


# ==============================================================================
# WebSocket Endpoint (/ws/live)
# ==============================================================================


@app.websocket("/ws/live")
async def websocket_live_endpoint(
    websocket: WebSocket,
    ticket: Optional[str] = Query(default=None),
):
    """Multiplexed real-time WebSocket connection for streaming prices, orders, positions, and health.

    Enforces authentication via:
    1. Single-use ticket query parameter (`?ticket=...`)
    2. Or initial JSON auth frame within 5 seconds: `{"type": "AUTH", "token": "..."}` or `{"type": "AUTH", "ticket": "..."}`.
    """
    services = get_services()
    user: Optional[UserPrincipal] = None

    # Step 1: Validate query ticket if supplied
    if ticket:
        user = await services.auth_svc.validate_and_consume_ws_ticket(ticket)

    await websocket.accept()

    # Step 2: If not authenticated via query, await initial auth frame
    if not user:
        try:
            raw_msg = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            auth_frame = json.loads(raw_msg)
            if auth_frame.get("type") == "AUTH":
                if "ticket" in auth_frame:
                    user = await services.auth_svc.validate_and_consume_ws_ticket(auth_frame["ticket"])
                elif "token" in auth_frame:
                    user = services.auth_svc.verify_access_token(auth_frame["token"])
        except Exception as e:
            logger.warning("WebSocket handshake authentication failed: %s", e)

    # Step 3: Reject unauthenticated connections with code 4401
    if not user:
        from libs.config.settings import AppEnv
        if ticket is None and services.settings.app_env == AppEnv.DEVELOPMENT:
            user = UserPrincipal(
                user_id="dev-viewer",
                username="dev_user",
                role=UserRole.READ_ONLY,
                is_active=True,
            )
            logger.info("WebSocket auto-authenticated development viewer session.")
        else:
            try:
                await websocket.send_text(
                    json.dumps({
                        "type": "AUTH_ERROR",
                        "code": 4401,
                        "message": "WebSocket authentication failed: invalid, missing, or expired token/ticket.",
                        "timestamp": utc_now().isoformat(),
                    })
                )
                await websocket.close(code=4401)
            except Exception:
                pass
            return

    # User authenticated! Register connection
    await ws_manager.register(websocket)
    try:
        # Acknowledge authentication success
        await websocket.send_text(
            json.dumps({
                "type": "AUTH_OK",
                "user": user.username,
                "role": user.role.value,
                "timestamp": utc_now().isoformat(),
            })
        )

        # Send immediate initial state handshake
        health = await get_system_health()
        await websocket.send_text(
            json.dumps({"type": "SYSTEM_HEALTH", "data": health, "timestamp": utc_now().isoformat()})
        )
        pnl = await services.portfolio_svc.get_pnl_summary()
        await websocket.send_text(
            json.dumps({"type": "PNL", "data": pnl, "timestamp": utc_now().isoformat()})
        )

        while True:
            # Keep connection alive and receive client commands (e.g. ping/pong, subscribe)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "PING":
                    await websocket.send_text(
                        json.dumps({"type": "PONG", "timestamp": utc_now().isoformat()})
                    )
            except Exception:
                pass
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
        await ws_manager.disconnect(websocket)
