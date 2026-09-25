"""Comprehensive test suite for ICICI Breeze Clean Architecture integration.

Verifies:
1. Pure domain models with strict Decimal arithmetic.
2. Request and Datetime mapping to Breeze conventions.
3. Response envelope unwrapping and hierarchical error taxonomy mapping.
4. SdkRunner bounded off-loop worker execution.
5. Multi-tier token-bucket operational rate limiting.
6. Durable request ledger idempotency, duplicate suppression, and hash collision detection.
7. ExecutionGuard timeout handling -> SUBMISSION_UNKNOWN (no blind retries).
8. End-to-end clean architecture service orchestration with mock SDK.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import uuid

from pydantic import SecretStr
import pytest

from services.broker_gateway.application.execution_guard import (
    ExecutionGuard,
    compute_payload_hash,
)
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.domain.enums import (
    BrokerWriteAction,
    BrokerWriteStatus,
    Exchange,
    FeedInterval,
    OptionRight,
    OrderSide,
    OrderStyle,
    OrderValidity,
    ProductType,
    SessionStatus,
)
from services.broker_gateway.domain.errors import (
    BrokerAuthenticationError,
    BrokerOrderRejectedError,
    BrokerRateLimitError,
    BrokerSessionExpiredError,
    BrokerSubmissionUnknownError,
    BrokerTimeoutError,
    BrokerValidationError,
)
from services.broker_gateway.domain.models.account import FundsSnapshot, MarginSnapshot
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.market_data import Candle, OptionChainSnapshot, Quote
from services.broker_gateway.domain.models.orders import (
    BrokerOrderAcknowledgement,
    BrokerOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
    SquareOffRequest,
)
from services.broker_gateway.domain.models.session import SessionCredentials
from services.broker_gateway.infrastructure.icici.adapters.account_adapter import BreezeAccountAdapter
from services.broker_gateway.infrastructure.icici.adapters.market_data_adapter import BreezeMarketDataAdapter
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.infrastructure.icici.adapters.session_adapter import BreezeSessionAdapter
from services.broker_gateway.infrastructure.icici.adapters.trading_adapter import BreezeTradingAdapter
from services.broker_gateway.infrastructure.icici.adapters.websocket_adapter import BreezeWebSocketAdapter
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.datetime_mapper import (
    parse_breeze_datetime,
    to_breeze_date_str,
    to_breeze_iso,
)
from services.broker_gateway.infrastructure.icici.request_mapper import (
    map_cancel_order_request,
    map_modify_order_request,
    map_place_order_request,
    map_square_off_request,
)
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.broker_gateway.infrastructure.icici.sdk_runner import SdkRunner
from services.broker_gateway.infrastructure.icici.status_mapper import normalize_breeze_order_status
from services.broker_gateway.infrastructure.persistence.request_ledger_repository import (
    BrokerRequestLedgerRepository,
)
from services.broker_gateway.infrastructure.rate_limit.policies import BrokerRateLimiter


# ==============================================================================
# 1. Domain Models & Decimal Arithmetic Tests
# ==============================================================================


def test_domain_models_decimal_preservation() -> None:
    """All domain monetary values must use Decimal without binary float drift."""
    inst = BrokerInstrumentRef(
        internal_instrument_id=uuid.uuid4(),
        exchange=Exchange.NFO,
        stock_code="NIFTY",
        product_type=ProductType.OPTIONS,
        expiry=date(2026, 9, 24),
        strike=Decimal("25000.50"),
        option_right=OptionRight.CALL,
        stock_token=None,
    )
    assert isinstance(inst.strike, Decimal)

    req = BrokerOrderRequest(
        request_id="REQ-001",
        account_id="ACC-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=50,
        order_style=OrderStyle.LIMIT,
        limit_price=Decimal("125.75"),
        stop_price=None,
        validity=OrderValidity.DAY,
        client_reference="CLIENT-001",
    )
    assert req.limit_price == Decimal("125.75")
    assert isinstance(req.limit_price, Decimal)

    quote = Quote(
        instrument=inst,
        ltp=Decimal("125.75"),
        best_bid_price=Decimal("125.50"),
        best_bid_qty=150,
        best_ask_price=Decimal("126.00"),
        best_ask_qty=200,
    )
    assert isinstance(quote.ltp, Decimal)
    assert quote.best_ask_price - quote.best_bid_price == Decimal("0.50")


# ==============================================================================
# 2. Datetime & Request Mappers
# ==============================================================================


def test_datetime_mapping_utc_and_ist() -> None:
    """Verify ISO and exchange date string formatting and parsing."""
    dt = datetime(2026, 9, 16, 9, 15, 0, tzinfo=timezone.utc)
    iso_str = to_breeze_iso(dt)
    assert iso_str == "2026-09-16T09:15:00.000Z"

    d_str = to_breeze_date_str(date(2026, 9, 16))
    assert d_str == "2026-09-16"

    # Breeze returns IST string
    parsed = parse_breeze_datetime("2026-09-16 15:30:00")
    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 10  # 15:30 IST is 10:00 UTC


def test_request_mapper_conversion() -> None:
    """Verify request mapper produces correct Breeze parameter dictionary."""
    inst = BrokerInstrumentRef(
        internal_instrument_id=uuid.uuid4(),
        exchange=Exchange.NFO,
        stock_code="CNXBAN",
        product_type=ProductType.OPTIONS,
        expiry=date(2026, 9, 25),
        strike=Decimal("52000"),
        option_right=OptionRight.PUT,
        stock_token=None,
    )
    req = BrokerOrderRequest(
        request_id="REQ-123",
        account_id="ACC-001",
        instrument=inst,
        side=OrderSide.SELL,
        quantity=30,
        order_style=OrderStyle.LIMIT,
        limit_price=Decimal("210.50"),
        stop_price=None,
        validity=OrderValidity.DAY,
        client_reference="ORD-REF-001",
    )
    params = map_place_order_request(req)
    assert params["stock_code"] == "CNXBAN"
    assert params["exchange_code"] == "NFO"
    assert params["product"] == "options"
    assert params["action"] == "sell"
    assert params["order_type"] == "limit"
    assert params["quantity"] == "30"
    assert params["price"] == "210.50"
    assert params["right"] == "put"
    assert params["strike_price"] == "52000"


def test_request_mapper_stop_limit_uses_distinct_trigger_and_limit() -> None:
    inst = BrokerInstrumentRef(
        internal_instrument_id=uuid.uuid4(),
        exchange=Exchange.NFO,
        stock_code="NIFTY",
        product_type=ProductType.OPTIONS,
        expiry=date(2026, 9, 29),
        strike=Decimal("25000"),
        option_right=OptionRight.CALL,
        stock_token=None,
    )
    req = BrokerOrderRequest(
        request_id="REQ-PROTECT-1",
        account_id="ACC-001",
        instrument=inst,
        side=OrderSide.SELL,
        quantity=65,
        order_style=OrderStyle.STOP_LIMIT,
        limit_price=Decimal("67.50"),
        stop_price=Decimal("75.00"),
        validity=OrderValidity.DAY,
        client_reference="PROTECT-1",
    )

    params = map_place_order_request(req)
    assert params["order_type"] == "stoploss"
    assert params["price"] == "67.50"
    assert params["stoploss"] == "75.00"
    assert params["action"] == "sell"


def test_status_mapper() -> None:
    """Verify raw Breeze strings normalize to canonical platform OrderState."""
    assert normalize_breeze_order_status("Executed").value == "FILLED"
    assert normalize_breeze_order_status("Complete").value == "FILLED"
    assert normalize_breeze_order_status("Cancelled").value == "CANCELLED"
    assert normalize_breeze_order_status("Rejected").value == "REJECTED"
    assert normalize_breeze_order_status("Part Executed").value == "PARTIALLY_FILLED"
    assert normalize_breeze_order_status("Open").value == "OPEN"
    assert normalize_breeze_order_status("UnknownStatus").value == "SUBMISSION_UNKNOWN"


# ==============================================================================
# 3. Response Validator & Error Taxonomy
# ==============================================================================


def test_response_validator_success() -> None:
    """Valid envelope unwraps data payload cleanly."""
    raw = {"Status": 200, "Success": {"order_id": "ORD-999"}, "Error": None}
    unwrapped = BreezeResponseValidator.unwrap_success(raw)
    assert unwrapped == {"order_id": "ORD-999"}


def test_response_validator_error_mapping() -> None:
    """Breeze error envelopes raise typed domain exceptions."""
    # Session Expired
    with pytest.raises(BrokerSessionExpiredError):
        BreezeResponseValidator.unwrap_success(
            {"Status": 500, "Error": "Session Token Expired. Please login again.", "Success": None}
        )

    # Authentication Failed
    with pytest.raises(BrokerAuthenticationError):
        BreezeResponseValidator.unwrap_success(
            {"Status": 401, "Error": "Invalid API Key or Secret", "Success": None}
        )

    # Rate limit exceeded
    with pytest.raises(BrokerRateLimitError):
        BreezeResponseValidator.unwrap_success(
            {"Status": 429, "Error": "Maximum request limit per minute exceeded", "Success": None}
        )

    # Order rejection
    with pytest.raises(BrokerOrderRejectedError):
        BreezeResponseValidator.unwrap_success(
            {"Status": 500, "Error": "Margin insufficient to place order", "Success": None}
        )


# ==============================================================================
# 4. SdkRunner Off-Loop Execution & Timeouts
# ==============================================================================


@pytest.mark.asyncio
async def test_sdk_runner_off_loop_execution() -> None:
    """SdkRunner executes synchronous work in a worker thread off the main event loop."""
    runner = SdkRunner()

    def _sync_work():
        time.sleep(0.05)
        return "completed"

    result = await runner.run(_sync_work, timeout_sec=1.0)
    assert result == "completed"


@pytest.mark.asyncio
async def test_sdk_runner_timeout() -> None:
    """SdkRunner raises BrokerTimeoutError or TimeoutError when blocking call exceeds timeout."""
    runner = SdkRunner()

    def _slow_work():
        time.sleep(0.5)
        return "done"

    with pytest.raises((BrokerTimeoutError, TimeoutError)):
        await runner.run(_slow_work, timeout_sec=0.1)



# ==============================================================================
# 5. Operational Rate Limiter
# ==============================================================================


@pytest.mark.asyncio
async def test_rate_limiter_permits() -> None:
    """Rate limiter allows requests under capacity and throttles when depleted."""
    limiter = BrokerRateLimiter(calls_per_minute=10, writes_per_second=2)

    # 2 writes succeed immediately
    await limiter.acquire_write()
    await limiter.acquire_write()

    # 3rd write will throttle for replenishment
    start = time.monotonic()
    await limiter.acquire_write()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4  # throttled appropriately


# ==============================================================================
# 6. Durable Request Ledger & Idempotency
# ==============================================================================


@pytest.mark.asyncio
async def test_request_ledger_lifecycle_and_replay(tmp_path: Path) -> None:
    """Ledger handles reservation, replay, and collision detection correctly."""
    db_file = tmp_path / "ledger_test.db"
    repo = BrokerRequestLedgerRepository(db_path=db_file)
    await repo.initialize()

    # Apply migration schema directly for test isolation
    async with repo.engine.connect() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_write_requests (
                request_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                client_reference TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                state TEXT NOT NULL,
                broker_order_id TEXT,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await conn.commit()

    payload = {"symbol": "NIFTY", "qty": 50, "price": "100.0"}
    h = compute_payload_hash(payload)

    # 1. New reservation
    is_new, cached = await repo.record_intent(
        request_id="REQ-TEST-1",
        account_id="ACC-1",
        client_reference="REF-1",
        action=BrokerWriteAction.PLACE,
        request_hash=h,
        payload=payload,
    )
    assert is_new is True
    assert cached is None

    # 2. Mark submitting
    await repo.mark_submitting("REQ-TEST-1")
    req = await repo.get_request("REQ-TEST-1")
    assert req["state"] == BrokerWriteStatus.SUBMITTING.value

    # 3. Mark acknowledged
    await repo.mark_acknowledged(
        request_id="REQ-TEST-1",
        broker_order_id="BRK-ORD-888",
        result={"broker_order_id": "BRK-ORD-888", "message": "Success"},
    )
    req = await repo.get_request("REQ-TEST-1")
    assert req["state"] == BrokerWriteStatus.ACKNOWLEDGED.value
    assert req["broker_order_id"] == "BRK-ORD-888"

    # 4. Idempotent replay of same request
    is_new2, cached2 = await repo.record_intent(
        request_id="REQ-TEST-1",
        account_id="ACC-1",
        client_reference="REF-1",
        action=BrokerWriteAction.PLACE,
        request_hash=h,
        payload=payload,
    )
    assert is_new2 is False
    assert cached2 is not None
    assert cached2.broker_order_id == "BRK-ORD-888"
    assert cached2.status == BrokerWriteStatus.ACKNOWLEDGED

    # 5. Payload hash collision detection
    diff_payload = {"symbol": "NIFTY", "qty": 100, "price": "100.0"}
    diff_h = compute_payload_hash(diff_payload)
    with pytest.raises(BrokerValidationError, match="Idempotency key collision"):
        await repo.record_intent(
            request_id="REQ-TEST-1",
            account_id="ACC-1",
            client_reference="REF-1",
            action=BrokerWriteAction.PLACE,
            request_hash=diff_h,
            payload=diff_payload,
        )


# ==============================================================================
# 7. ExecutionGuard & SUBMISSION_UNKNOWN Recovery
# ==============================================================================


@pytest.mark.asyncio
async def test_execution_guard_timeout_converts_to_submission_unknown(tmp_path: Path) -> None:
    """Timeout during broker order write must be marked SUBMISSION_UNKNOWN with no blind retries."""
    db_file = tmp_path / "guard_test.db"
    repo = BrokerRequestLedgerRepository(db_path=db_file)
    await repo.initialize()

    async with repo.engine.connect() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_write_requests (
                request_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                client_reference TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                state TEXT NOT NULL,
                broker_order_id TEXT,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await conn.commit()

    # Mock active session
    mock_session_port = MagicMock()
    mock_session_snap = MagicMock()
    mock_session_snap.status = SessionStatus.ACTIVE
    mock_session_port.get_status = AsyncMock(return_value=mock_session_snap)


    guard = ExecutionGuard(ledger=repo, session_port=mock_session_port)

    # Function simulating broker network timeout
    async def _timed_out_write():
        raise BrokerTimeoutError("Gateway timeout connecting to Breeze API")

    req_id = "TIMEOUT-ORDER-001"
    with pytest.raises(BrokerSubmissionUnknownError, match="outcome is unknown due to timeout"):
        await guard.execute_guarded_write(
            request_id=req_id,
            account_id="ACC-TEST",
            client_reference="REF-TEST",
            action=BrokerWriteAction.PLACE,
            payload={"stock": "NIFTY", "qty": 50},
            execute_fn=_timed_out_write,
        )

    # Verify write record in ledger is strictly SUBMISSION_UNKNOWN
    record = await repo.get_request(req_id)
    assert record is not None
    assert record["state"] == BrokerWriteStatus.SUBMISSION_UNKNOWN.value

    # Subsequent blind retry MUST be rejected
    with pytest.raises(BrokerSubmissionUnknownError, match="Reconciliation required before retry"):
        await guard.execute_guarded_write(
            request_id=req_id,
            account_id="ACC-TEST",
            client_reference="REF-TEST",
            action=BrokerWriteAction.PLACE,
            payload={"stock": "NIFTY", "qty": 50},
            execute_fn=_timed_out_write,
        )


# ==============================================================================
# 8. End-to-End Clean Architecture Service Integration
# ==============================================================================


@pytest.mark.asyncio
async def test_clean_architecture_service_orchestration(tmp_path: Path) -> None:
    """Full orchestration test of ports, adapters, and application service with a mock SDK."""
    # 1. Setup mock Breeze SDK instance
    mock_sdk = MagicMock()
    mock_sdk.generate_session = MagicMock(return_value={"Success": "Session OK", "Status": 200, "Error": None})
    mock_sdk.get_funds = MagicMock(
        return_value={
            "Status": 200,
            "Success": {"bank_balance": "500000.00", "cash_available": "450000.00", "margin_used": "50000.00"},
            "Error": None,
        }
    )
    mock_sdk.get_quotes = MagicMock(
        return_value={
            "Status": 200,
            "Success": [
                {
                    "ltp": "175.50",
                    "best_bid_price": "175.25",
                    "best_ask_price": "175.75",
                    "total_quantity_traded": 12000,
                    "open_interest": 45000,
                    "datetime": "2026-09-16 10:30:00",
                }
            ],
            "Error": None,
        }
    )
    mock_sdk.place_order = MagicMock(
        return_value={
            "Status": 200,
            "Success": {"order_id": "BREEZE-20260916-1001", "message": "Order placed successfully"},
            "Error": None,
        }
    )
    mock_sdk.get_order_list = MagicMock(
        return_value={
            "Status": 200,
            "Success": [
                {
                    "order_id": "BREEZE-20260916-1001",
                    "stock_code": "NIFTY",
                    "exchange_code": "NFO",
                    "action": "buy",
                    "order_type": "limit",
                    "quantity": 50,
                    "executed_quantity": 50,
                    "price": "175.50",
                    "average_price": "175.50",
                    "status": "Executed",
                    "order_date": "2026-09-16 10:31:00",
                }
            ],
            "Error": None,
        }
    )

    # 2. Wire clean architecture components
    client_mgr = BreezeClientManager(custom_sdk_instance=mock_sdk)
    limiter = BrokerRateLimiter(calls_per_minute=90, writes_per_second=8)

    acc_adapter = BreezeAccountAdapter(client_manager=client_mgr, rate_limiter=limiter)
    sess_adapter = BreezeSessionAdapter(client_manager=client_mgr, account_adapter=acc_adapter)
    mkt_adapter = BreezeMarketDataAdapter(client_manager=client_mgr, rate_limiter=limiter)
    trd_adapter = BreezeTradingAdapter(client_manager=client_mgr, rate_limiter=limiter)
    ws_adapter = BreezeWebSocketAdapter(client_manager=client_mgr)

    ledger_db = tmp_path / "service_test_ledger.db"
    ledger_repo = BrokerRequestLedgerRepository(db_path=ledger_db)
    await ledger_repo.initialize()

    async with ledger_repo.engine.connect() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_write_requests (
                request_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                client_reference TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                state TEXT NOT NULL,
                broker_order_id TEXT,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await conn.commit()

    guard = ExecutionGuard(ledger=ledger_repo, session_port=sess_adapter)

    app_service = BrokerApplicationService(
        session_port=sess_adapter,
        account_port=acc_adapter,
        market_data_port=mkt_adapter,
        trading_port=trd_adapter,
        stream_port=ws_adapter,
        ledger=ledger_repo,
        execution_guard=guard,
    )

    # 3. Activate session
    creds = SessionCredentials(
        api_key="TEST_API_KEY",
        secret_key=SecretStr("TEST_SECRET_KEY"),
        session_token=SecretStr("TEST_SESSION_TOKEN"),
    )
    status_snap = await app_service.activate_session(creds)
    assert status_snap.status == SessionStatus.ACTIVE

    # 4. Verify funds query returns Decimal
    funds = await app_service.get_funds()
    assert isinstance(funds.available_margin, Decimal)
    assert funds.available_margin == Decimal("450000.00")
    assert funds.used_margin == Decimal("50000.00")

    # 5. Verify market quote returns Quote with Decimal
    inst = BrokerInstrumentRef(
        internal_instrument_id=uuid.uuid4(),
        exchange=Exchange.NFO,
        stock_code="NIFTY",
        product_type=ProductType.OPTIONS,
        expiry=date(2026, 9, 24),
        strike=Decimal("25000"),
        option_right=OptionRight.CALL,
        stock_token=None,
    )
    quote = await app_service.get_quote(inst)
    assert isinstance(quote.ltp, Decimal)
    assert quote.ltp == Decimal("175.50")
    assert quote.best_bid_price == Decimal("175.25")

    # 6. Place order through ExecutionGuard
    order_req = BrokerOrderRequest(
        request_id="E2E-ORDER-001",
        account_id="TEST_API",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=50,
        order_style=OrderStyle.LIMIT,
        limit_price=Decimal("175.50"),
        stop_price=None,
        validity=OrderValidity.DAY,
        client_reference="E2E-ORDER-001",
    )
    ack = await app_service.place_order(order_req)
    assert ack.status == BrokerWriteStatus.ACKNOWLEDGED
    assert ack.broker_order_id == "BREEZE-20260916-1001"

    # 7. Verify order reconciliation
    orders = await app_service.get_orders()
    assert len(orders) == 1
    assert orders[0].broker_order_id == "BREEZE-20260916-1001"
    assert orders[0].normalized_status == "FILLED"
    assert orders[0].raw_status == "Executed"
    assert isinstance(orders[0].price, Decimal)
    assert orders[0].price == Decimal("175.50")


def test_breeze_future_quote_accepts_official_singular_product_type() -> None:
    row = {
        "exchange_code": "NFO",
        "product_type": "Future",
        "stock_code": "NIFTY",
        "expiry_date": "29-Sep-2026",
        "ltp": 23480.25,
        "ltt": "21-Sep-2026 15:29:59",
    }
    assert IciciBreezeAdapter._is_valid_futures_quote(row) is True
    assert IciciBreezeAdapter._is_valid_futures_quote({**row, "product_type": "Futures"}) is True
    assert IciciBreezeAdapter._is_valid_futures_quote({**row, "exchange_code": "NSE"}) is False


@pytest.mark.asyncio
async def test_breeze_market_adapter_maps_official_offer_volume_and_oi_change_fields() -> None:
    sdk = MagicMock()
    sdk.get_option_chain_quotes.side_effect = [
        {
            "Status": 200,
            "Success": [{
                "right": "Call", "strike_price": 23400.0, "ltp": 120.0,
                "best_bid_price": 119.5, "best_offer_price": 120.5,
                "total_quantity_traded": "12345", "open_interest": 45678.0,
                "chnge_oi": 321.0, "spot_price": "23414.3",
            }],
            "Error": None,
        },
        {"Status": 200, "Success": [], "Error": None},
    ]
    client = BreezeClientManager(custom_sdk_instance=sdk)
    client._status = SessionStatus.ACTIVE
    adapter = BreezeMarketDataAdapter(client_manager=client)
    snapshot = await adapter.get_option_chain("NIFTY", date(2026, 9, 22))
    assert len(snapshot.contracts) == 1
    contract = snapshot.contracts[0]
    assert contract.bid == Decimal("119.5")
    assert contract.ask == Decimal("120.5")
    assert contract.volume == 12345
    assert contract.open_interest == 45678
    assert contract.oi_change == 321


@pytest.mark.asyncio
async def test_breeze_quote_maps_best_offer_as_ask() -> None:
    sdk = MagicMock()
    sdk.get_quotes.return_value = {
        "Status": 200,
        "Success": [{
            "ltp": "100.0", "best_bid_price": "99.5",
            "best_offer_price": "100.5", "best_offer_quantity": "65",
            "datetime": "2026-09-21 10:00:00",
        }],
        "Error": None,
    }
    client = BreezeClientManager(custom_sdk_instance=sdk)
    client._status = SessionStatus.ACTIVE
    adapter = BreezeMarketDataAdapter(client_manager=client)
    instrument = BrokerInstrumentRef(
        internal_instrument_id=uuid.uuid4(),
        exchange=Exchange.NFO,
        stock_code="NIFTY",
        product_type=ProductType.OPTIONS,
        expiry=date(2026, 9, 22),
        strike=Decimal("23400"),
        option_right=OptionRight.CALL,
        stock_token=None,
    )
    quote = await adapter.get_quote(instrument)
    assert quote.best_ask_price == Decimal("100.5")
    assert quote.best_ask_qty == 65


def test_breeze_security_master_resolves_nearest_future_without_calendar_guessing() -> None:
    sdk = MagicMock()
    sdk.stock_script_dict_list = [{}, {}, {}, {}, {
        "FUT-NIFTY-29-Sep-2026": "50123",
        "FUT-NIFTY-27-Oct-2026": "50124",
        "OPT-NIFTY-22-Sep-2026-23400-CE": "60123",
    }]
    result = IciciBreezeAdapter._future_from_security_master(
        sdk, "NIFTY", date(2026, 9, 21)
    )
    assert result is not None
    assert result["expiry"] == date(2026, 9, 29)
    assert result["broker_token"] == "50123"


@pytest.mark.asyncio
async def test_breeze_resolve_nearest_future_prefers_security_master() -> None:
    sdk = MagicMock()
    sdk.stock_script_dict_list = [{}, {}, {}, {}, {
        "FUT-NIFTY-29-Sep-2026": "50123",
        "FUT-NIFTY-27-Oct-2026": "50124",
    }]
    adapter = IciciBreezeAdapter(custom_sdk_instance=sdk)
    adapter.client_manager._status = SessionStatus.ACTIVE
    resolved = await adapter.resolve_nearest_future("NIFTY")
    assert resolved is not None
    assert resolved["expiry"] == "2026-09-29"
    assert resolved["broker_token"] == "50123"
    sdk.get_quotes.assert_not_called()


@pytest.mark.asyncio
async def test_breeze_no_positions_response_is_empty_portfolio() -> None:
    sdk = MagicMock()
    sdk.get_portfolio_positions.return_value = {
        "Status": 500,
        "Success": None,
        "Error": "No Positions available.",
    }
    client = BreezeClientManager(custom_sdk_instance=sdk)
    adapter = BreezeTradingAdapter(client_manager=client)

    positions = await adapter.get_positions()

    assert positions == []
