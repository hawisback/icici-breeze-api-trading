"""ICICI Breeze API Adapter implementing BrokerAdapter protocol backed by Clean Architecture.

This adapter bridges the high-level BrokerAdapter protocol with the clean architecture
domain, application, and infrastructure layers (BreezeClientManager, SdkRunner,
BreezeTradingAdapter, BrokerRequestLedgerRepository, and ExecutionGuard).
"""

from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from time import monotonic
from pathlib import Path
from typing import Optional
import uuid

from pydantic import SecretStr

from libs.broker_models.adapter import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerTradeResponse,
)
from libs.config.settings import get_settings
from libs.contracts.models import utc_now
from services.broker_gateway.application.execution_guard import ExecutionGuard
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.domain.enums import (
    BrokerWriteStatus,
    Exchange,
    OptionRight,
    OrderSide,
    OrderStyle,
    OrderValidity,
    ProductType,
    SessionStatus,
)
from services.broker_gateway.domain.errors import (
    BrokerOrderRejectedError,
    BrokerSubmissionUnknownError,
    BrokerTimeoutError,
)
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.orders import (
    BrokerOrderRequest as DomainOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
)
from services.broker_gateway.domain.models.session import SessionCredentials
from services.broker_gateway.infrastructure.icici.adapters.account_adapter import BreezeAccountAdapter
from services.broker_gateway.infrastructure.icici.adapters.market_data_adapter import BreezeMarketDataAdapter
from services.broker_gateway.infrastructure.icici.adapters.session_adapter import BreezeSessionAdapter
from services.broker_gateway.infrastructure.icici.adapters.trading_adapter import BreezeTradingAdapter
from services.broker_gateway.infrastructure.icici.adapters.websocket_adapter import BreezeWebSocketAdapter
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.sdk_runner import SdkRunner
from services.broker_gateway.infrastructure.persistence.request_ledger_repository import (
    BrokerRequestLedgerRepository,
)
from services.broker_gateway.infrastructure.rate_limit.policies import BrokerRateLimiter

logger = logging.getLogger(__name__)


class IciciBreezeAdapter(BrokerAdapter):
    """Clean Architecture ICICI Breeze Adapter with durable idempotency, rate limiting, and thread isolation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        session_token: Optional[str] = None,
        timeout: float = 8.0,
        db_path: Optional[Path] = None,
        custom_sdk_instance: Optional[object] = None,
    ) -> None:
        self.api_key = api_key or ""
        self.secret_key = secret_key or ""
        self.session_token = session_token or ""
        self.timeout = timeout
        self._resolved_future_cache: dict[str, tuple[date, dict[str, object]]] = {}
        self._future_resolution_retry_after: dict[str, float] = {}

        # Clean Architecture Components
        self.sdk_runner = SdkRunner()
        self.client_manager = BreezeClientManager(
            sdk_runner=self.sdk_runner,
            custom_sdk_instance=custom_sdk_instance,
        )
        self.rate_limiter = BrokerRateLimiter(
            calls_per_minute=get_settings().breeze_calls_per_minute,
            calls_per_day=get_settings().breeze_calls_per_day,
            writes_per_second=get_settings().breeze_writes_per_second,
        )

        self.account_adapter = BreezeAccountAdapter(
            client_manager=self.client_manager,
            rate_limiter=self.rate_limiter,
        )
        self.session_adapter = BreezeSessionAdapter(
            client_manager=self.client_manager,
            account_adapter=self.account_adapter,
        )
        self.market_adapter = BreezeMarketDataAdapter(
            client_manager=self.client_manager,
            rate_limiter=self.rate_limiter,
        )
        self.trading_adapter = BreezeTradingAdapter(
            client_manager=self.client_manager,
            rate_limiter=self.rate_limiter,
        )
        self.stream_adapter = BreezeWebSocketAdapter(
            client_manager=self.client_manager,
        )

        self.ledger_repo = BrokerRequestLedgerRepository(db_path=db_path)
        self.execution_guard = ExecutionGuard(
            ledger=self.ledger_repo,
            session_port=self.session_adapter,
        )

        self.clean_service = BrokerApplicationService(
            session_port=self.session_adapter,
            account_port=self.account_adapter,
            market_data_port=self.market_adapter,
            trading_port=self.trading_adapter,
            stream_port=self.stream_adapter,
            ledger=self.ledger_repo,
            execution_guard=self.execution_guard,
        )

    async def initialize(self) -> None:
        """Initialize database engine and dependencies."""
        await self.ledger_repo.initialize()

    async def authenticate(self, api_key: str, secret_key: str, session_token: str) -> bool:
        """Activate daily session using credentials."""
        api_key_str = api_key.get_secret_value() if isinstance(api_key, SecretStr) else str(api_key)
        secret_key_str = secret_key.get_secret_value() if isinstance(secret_key, SecretStr) else str(secret_key)
        session_token_str = session_token.get_secret_value() if isinstance(session_token, SecretStr) else str(session_token)

        self.api_key = api_key_str
        self.secret_key = secret_key_str
        self.session_token = session_token_str

        try:
            creds = SessionCredentials(
                api_key=api_key_str,
                secret_key=SecretStr(secret_key_str),
                session_token=SecretStr(session_token_str),
            )
            snapshot = await self.session_adapter.activate(creds)
            return snapshot.status == SessionStatus.ACTIVE
        except Exception as exc:
            logger.error("Authentication failed for Breeze adapter: %s", exc)
            return False

    @property
    def is_active(self) -> bool:
        return bool(self.client_manager.is_active)

    @staticmethod
    def _monthly_expiry_candidates(year: int, month: int) -> list[date]:
        last = date(year, month, monthrange(year, month)[1])
        nominal = last - timedelta(days=(last.weekday() - 1) % 7)
        values: list[date] = []
        cursor = nominal
        while len(values) < 5:
            if cursor.weekday() < 5:
                values.append(cursor)
            cursor -= timedelta(days=1)
        return values

    async def resolve_nearest_future(self, underlying: str = "NIFTY") -> Optional[dict[str, object]]:
        """Resolve the actual live near-month future by asking Breeze."""
        if not self.is_active:
            return None
        clean = "CNXBAN" if "BANK" in underlying.upper() else "NIFTY"
        ist = timezone(timedelta(hours=5, minutes=30))
        today = datetime.now(ist).date()
        key = f"{clean}:{today.isoformat()}"
        cached = self._resolved_future_cache.get(key)
        if cached and cached[0] == today:
            return dict(cached[1])
        if monotonic() < self._future_resolution_retry_after.get(key, 0.0):
            return None

        months: list[tuple[int, int]] = []
        year, month = today.year, today.month
        for _ in range(3):
            months.append((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

        sdk = self.client_manager.get_sdk_client()
        for year, month in months:
            for expiry in self._monthly_expiry_candidates(year, month):
                if expiry < today:
                    continue
                try:
                    await self.rate_limiter.acquire_read()
                    raw = await self.client_manager.sdk_runner.run(
                        lambda e=expiry: sdk.get_quotes(
                            stock_code=clean, exchange_code="NFO", product_type="futures",
                            expiry_date=f"{e.isoformat()}T06:00:00.000Z",
                            right="others", strike_price="0",
                        ),
                        timeout_sec=10.0,
                    )
                except Exception as exc:
                    logger.debug("Breeze futures candidate %s failed: %s", expiry, exc)
                    continue
                rows = raw.get("Success", []) if isinstance(raw, dict) else []
                if not isinstance(rows, list):
                    continue
                valid = next((row for row in rows
                    if str(row.get("exchange_code", "")).upper() == "NFO"
                    and str(row.get("product_type", "")).lower() == "futures"
                    and (float(row.get("ltp") or 0) > 0 or str(row.get("ltt") or "").strip().upper() not in {"", "NA"})
                ), None)
                if valid is None:
                    continue
                resolved: dict[str, object] = {
                    "underlying": "BANKNIFTY" if clean == "CNXBAN" else "NIFTY",
                    "expiry": expiry.isoformat(), "stock_code": clean, "symbol": clean,
                    "exchange": "NFO", "broker": "ICICI_BREEZE", "broker_token": None,
                    "lot_size": 1, "tick_size": 0.05,
                }
                self._resolved_future_cache[key] = (today, resolved)
                self._future_resolution_retry_after.pop(key, None)
                logger.info("Resolved Breeze active %s future: expiry=%s", clean, expiry)
                return dict(resolved)
        self._future_resolution_retry_after[key] = monotonic() + 60.0
        logger.warning("Breeze could not validate an active %s futures contract", clean)
        return None

    async def get_funds(self) -> BrokerFunds:
        """Query funds balance via Account Adapter or return mock for test keys."""
        if not self.api_key or self.api_key.startswith("test_"):
            return BrokerFunds(
                available_margin=250_000.0,
                total_cash=300_000.0,
                used_margin=50_000.0,
            )

        try:
            funds_snap = await self.account_adapter.get_funds()
            return BrokerFunds(
                available_margin=float(funds_snap.available_margin),
                total_cash=float(funds_snap.total_cash),
                used_margin=float(funds_snap.used_margin),
            )
        except Exception as exc:
            logger.warning("Error fetching Breeze funds: %s", exc)
            return BrokerFunds(available_margin=0.0, total_cash=0.0, used_margin=0.0)

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResponse:
        """Submit order via clean architecture ExecutionGuard with durable idempotency."""
        if not self.api_key or self.api_key.startswith("test_"):
            return BrokerOrderResponse(
                success=True,
                broker_order_id=f"BREEZE-{utc_now().strftime('%Y%m%d%H%M%S')}",
                client_order_id=request.client_order_id,
                status="PLACED",
                message="Order placed in Breeze test harness",
            )

        # Convert to Clean Architecture Domain Model
        exch = Exchange.NFO if request.exchange_code.upper() == "NFO" else Exchange.NSE
        side = OrderSide.BUY if request.action.lower() == "buy" else OrderSide.SELL
        order_style = OrderStyle.STOP_LIMIT if request.order_type.lower() in ("stoploss", "stop_limit") else OrderStyle.LIMIT

        right = None
        if request.right:
            right = OptionRight.CALL if "call" in request.right.lower() else OptionRight.PUT

        inst = BrokerInstrumentRef(
            internal_instrument_id=uuid.uuid4(),
            exchange=exch,
            stock_code=request.stock_code,
            product_type=ProductType.OPTIONS if request.product.lower() == "options" else ProductType.CASH,
            expiry=datetime.strptime(request.expiry_date, "%Y-%m-%d").date() if request.expiry_date else None,
            strike=Decimal(str(request.strike_price)) if request.strike_price else None,
            option_right=right,
            stock_token=None,
        )

        domain_req = DomainOrderRequest(
            request_id=request.client_order_id,
            account_id=self.api_key[:8] if self.api_key else "DEFAULT",
            instrument=inst,
            side=side,
            quantity=request.quantity,
            order_style=order_style,
            limit_price=Decimal(str(request.price)),
            stop_price=Decimal(str(request.price)) if order_style == OrderStyle.STOP_LIMIT else None,
            validity=OrderValidity.DAY if request.validity.lower() == "day" else OrderValidity.IOC,
            client_reference=request.client_order_id,
            user_remark=request.user_remark,
        )

        try:
            ack = await self.clean_service.place_order(domain_req)
            return BrokerOrderResponse(
                success=True,
                broker_order_id=ack.broker_order_id,
                client_order_id=request.client_order_id,
                status="PLACED" if ack.status == BrokerWriteStatus.ACKNOWLEDGED else ack.status.value,
                message=ack.message,
            )
        except BrokerSubmissionUnknownError as exc:
            logger.error("Breeze place_order SUBMISSION_UNKNOWN for %s: %s", request.client_order_id, exc)
            return BrokerOrderResponse(
                success=False,
                client_order_id=request.client_order_id,
                status="UNKNOWN",
                message="Order submission outcome unknown due to timeout. Do not blind retry.",
            )
        except BrokerOrderRejectedError as exc:
            logger.warning("Breeze place_order rejected for %s: %s", request.client_order_id, exc)
            return BrokerOrderResponse(
                success=False,
                client_order_id=request.client_order_id,
                status="REJECTED",
                message=str(exc),
            )
        except Exception as exc:
            logger.error("Breeze place_order error for %s: %s", request.client_order_id, exc)
            return BrokerOrderResponse(
                success=False,
                client_order_id=request.client_order_id,
                status="REJECTED",
                message=str(exc),
            )

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
    ) -> BrokerOrderResponse:
        """Modify order via clean service."""
        if not self.api_key or self.api_key.startswith("test_"):
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="MODIFIED",
                message="Breeze order modified",
            )

        req = ModifyBrokerOrderRequest(
            request_id=f"MOD-{broker_order_id}-{int(datetime.now(timezone.utc).timestamp())}",
            broker_order_id=broker_order_id,
            quantity=quantity,
            limit_price=Decimal(str(price)) if price is not None else None,
        )
        try:
            ack = await self.clean_service.modify_order(req)
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="MODIFIED",
                message=ack.message,
            )
        except Exception as exc:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="REJECTED",
                message=str(exc),
            )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        """Cancel order via clean service."""
        if not self.api_key or self.api_key.startswith("test_"):
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="CANCELLED",
                message="Breeze order cancelled",
            )

        req = CancelBrokerOrderRequest(
            request_id=f"CAN-{broker_order_id}-{int(datetime.now(timezone.utc).timestamp())}",
            broker_order_id=broker_order_id,
        )
        try:
            ack = await self.clean_service.cancel_order(req)
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="CANCELLED",
                message=ack.message,
            )
        except Exception as exc:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="REJECTED",
                message=str(exc),
            )

    async def get_order_status(self, broker_order_id: str) -> Optional[BrokerOrderResponse]:
        """Fetch order status detail."""
        if not self.api_key or self.api_key.startswith("test_"):
            return None

        order = await self.clean_service.get_order_detail(broker_order_id)
        if not order:
            return None

        return BrokerOrderResponse(
            success=True,
            broker_order_id=order.broker_order_id,
            client_order_id=order.client_reference or "",
            status=order.normalized_status,
            message=order.raw_status,
        )

    async def get_positions(self) -> list[BrokerPositionResponse]:
        """Fetch positions list."""
        if not self.api_key or self.api_key.startswith("test_"):
            return []

        positions = await self.clean_service.get_positions()
        return [
            BrokerPositionResponse(
                symbol=pos.instrument.stock_code,
                exchange=pos.instrument.exchange.value,
                quantity=pos.quantity,
                average_price=float(pos.average_price),
                ltp=float(pos.ltp),
                pnl=float(pos.total_pnl),
            )
            for pos in positions
        ]

    async def get_trades(self) -> list[BrokerTradeResponse]:
        """Fetch executed trade fills."""
        if not self.api_key or self.api_key.startswith("test_"):
            return []

        trades = await self.clean_service.get_trades()
        return [
            BrokerTradeResponse(
                trade_id=t.trade_id,
                broker_order_id=t.broker_order_id,
                symbol=t.instrument.stock_code,
                exchange=t.instrument.exchange.value,
                side=t.side.value,
                quantity=t.quantity,
                price=float(t.execution_price),
                executed_at=t.trade_time,
            )
            for t in trades
        ]


# Backward-compatible alias
RateLimiter = BrokerRateLimiter

