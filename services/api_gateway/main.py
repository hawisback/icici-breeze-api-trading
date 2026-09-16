"""FastAPI API Gateway and WebSocket Multiplexer.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
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
from libs.observability.logger import setup_logging
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

    await container.event_bus.subscribe(Topics.MARKET_QUOTE, on_quote_event)
    await container.event_bus.subscribe(Topics.MARKET_CANDLE, on_candle_event)
    await container.event_bus.subscribe(Topics.ORDER_STATE, on_order_event)
    await container.event_bus.subscribe(Topics.PORTFOLIO_POSITION, on_position_event)
    await container.event_bus.subscribe(Topics.PORTFOLIO_PNL, on_pnl_event)
    await container.event_bus.subscribe(Topics.AUDIT_EVENT, on_audit_event)

    yield

    # Shutdown
    logger.info("Shutting down API Gateway...")
    await container.market_svc.stop_simulated_feed()
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
    session_token: str
    account_id: str = "ICICI_PRIMARY"


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


# ==============================================================================
# REST Endpoints (/api/v1)
# ==============================================================================


@app.get("/api/v1/system/health")
async def get_system_health():
    """Aggregated health check of all platform services."""
    services = get_services()
    session_status = await services.session_svc.get_session_status()
    feed_status = services.market_svc.get_feed_status()
    system_mode = await services.risk_svc.get_system_mode()

    return {
        "status": "HEALTHY",
        "timestamp": utc_now().isoformat(),
        "services": {
            "api_gateway": "ONLINE",
            "broker_session": "CONNECTED" if session_status.get("connected") else "DISCONNECTED",
            "market_feed": feed_status["status"],
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



@app.get("/api/v1/session/status")
async def get_session_status():
    services = get_services()
    return await services.session_svc.get_session_status()


@app.post("/api/v1/session/login")
async def session_login(req: LoginRequest):
    services = get_services()
    result = await services.session_svc.activate_session(
        api_key=req.api_key,
        secret_key=req.secret_key,
        session_token=req.session_token,
        account_id=req.account_id,
    )
    return result


@app.get("/api/v1/account/funds")
async def get_account_funds(mode: TradingMode = TradingMode.PAPER):
    services = get_services()
    funds = await services.gateway_svc.get_funds(mode=mode)
    return funds.model_dump()


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


@app.post("/api/v1/live-gate/challenge")
async def request_live_gate_challenge(
    req: LiveGateChallengeRequest,
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()
    return await services.live_gate.request_activation_challenge(
        operator_id=req.operator_id,
        account_id=req.account_id,
        duration_minutes=req.duration_minutes,
    )


@app.post("/api/v1/live-gate/confirm")
async def confirm_live_gate(
    req: LiveGateConfirmRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    services = get_services()

    async def _execute():
        confirmed = await services.live_gate.confirm_activation(
            challenge_id=req.challenge_id,
            challenge_token=req.challenge_token,
            operator_id=req.operator_id,
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
            operator_id=req.operator_id,
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

