"""Strategy Service managing auto-trading strategies, contract selection, position management, and order generation.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Optional

from libs.contracts.models import (
    Candle,
    OrderIntent,
    OrderSide,
    OrderType,
    ProductType,
    Signal,
    SourceType,
    TimeInForce,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from libs.config.settings import get_platform_settings
from services.oms.service import OMSService
from services.strategy.contract_selector import ContractSelector
from services.strategy.execution_policy import (
    resolve_strategy_execution_policy,
)
from services.strategy.features import FeatureEngine
from services.strategy.futures_signal import resolve_active_futures_instrument
from services.strategy.models import (
    ActiveTrade,
    AutoTradingConfig,
    AutoTradingMode,
    DecisionLogEntry,
    GateBlockers,
    HistoricalReplaySource,
    MarketFeatures,
    OptionType,
    SimulationRequest,
    SimulationResult,
    StrategyName,
    StrategySignal,
    StrategyState,
    ThresholdOverrides,
    TradeDirection,
    TradeLifecycleState,
    TriggerCondition,
    StrategyTriggerDiagnostics,
    TriggerDiagnosticsResponse,
)
from services.strategy.position_manager import PositionManager, UnderlyingRiskSizer, calculate_realized_trade_r, underlying_r_for_price
from services.strategy.reason_codes import (
    BROKER_PROTECTIVE_STOP_UNAVAILABLE,
    OPTION_EMERGENCY_STOP,
    OPTION_EMERGENCY_STOP_OUTCOME_STATUS,
    OPTION_EMERGENCY_STOP_UNDERLYING_REASON,
    is_option_emergency_stop,
)
from services.strategy.repository import StrategyRepository
from services.strategy.simulation import SimulationEngine
from services.strategy.strategies.candidate_runtime import (
    strategy_c_signal_from_status,
    strategy_d_signal_from_status,
)
from services.strategy.strategies.pivot_vwap_scalp import PivotVwapScalpStrategy
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy
from services.strategy.strategy_c_shadow_monitor import StrategyCShadowMonitor
from services.strategy.strategy_d_paper_monitor import StrategyDPaperMonitor
from services.strategy.telemetry import StrategyAEvaluationRecord, StrategyATelemetryStore

logger = logging.getLogger(__name__)


class StrategyService:
    """Orchestrates NIFTY Intraday Options Auto-Trading strategies and lifecycle."""

    def __init__(
        self,
        oms_service: OMSService,
        repository: Optional[StrategyRepository] = None,
        event_bus: Optional[EventBus] = None,
        option_chain_service: Optional[Any] = None,
        market_data_service: Optional[Any] = None,
        historical_service: Optional[Any] = None,
    ) -> None:
        self.oms = oms_service
        self.repo = repository or StrategyRepository()
        self.bus = event_bus or get_event_bus()
        self.chain_svc = option_chain_service
        self.mkt_svc = market_data_service
        self.hist_svc = historical_service

        self.config = AutoTradingConfig()
        self.position_manager = PositionManager(self.config.risk, self.config.session, strategy_config=self.config.tunables)
        self.risk_sizer = UnderlyingRiskSizer(self.config.risk)
        self.contract_selector = ContractSelector(self.config.option_selection)
        self.strategy_c_shadow = StrategyCShadowMonitor(
            repository=self.repo,
            historical_service=self.hist_svc,
            option_chain_service=self.chain_svc,
            market_data_service=self.mkt_svc,
            contract_selector=self.contract_selector,
            risk_sizer=self.risk_sizer,
            risk_config=self.config.risk,
        )
        self._last_strategy_c_shadow_status: dict[str, Any] = {"status": "NOT_INITIALIZED"}
        self.strategy_d_paper = StrategyDPaperMonitor(
            repository=self.repo,
            historical_service=self.hist_svc,
            option_chain_service=self.chain_svc,
            market_data_service=self.mkt_svc,
            contract_selector=self.contract_selector,
            risk_config=self.config.risk,
            session_config=self.config.session,
        )
        self._last_strategy_d_paper_status: dict[str, Any] = {"status": "NOT_INITIALIZED"}

        self.strategy_a = TrendPullbackStrategy(config=self.config.tunables)
        self.strategy_e = PivotVwapScalpStrategy(self.config.tunables)
        self.strategy_b = VolatilityBreakoutStrategy(
            rvol_threshold=self.config.tunables.rvol_threshold,
            adx_threshold=self.config.tunables.strategy_b_adx_threshold,
            min_confirmation_score=self.config.tunables.strat_b_min_confirmation,
            box_max_height_atr=self.config.tunables.box_max_height_atr,
            bb_width_percentile_threshold=self.config.tunables.bb_width_percentile_threshold,
            lookback_bars=self.config.tunables.compression_lookback_bars,
            max_age_bars=self.config.tunables.box_max_age_bars,
            breakout_buffer_atr=self.config.tunables.breakout_buffer_atr,
            max_extension_atr=self.config.tunables.breakout_max_extension_atr,
            entry_start=self.config.session.strategy_b_no_new_trade_before,
            entry_end=self.config.session.no_new_trade_after,
        )
        self.simulation_engine = SimulationEngine(
            historical_service=self.hist_svc,
            risk_config=self.config.risk,
            session_config=self.config.session,
            tunables=self.config.tunables,
        )

        self._loop_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_features: Optional[MarketFeatures] = None
        self._active_trades_cache: list[ActiveTrade] = []
        self._last_loss_exit_time: Optional[datetime] = None
        self._active_overrides: ThresholdOverrides = ThresholdOverrides()
        self._market_snapshot = ([], [], [])
        self._strategy_e_futures_5m: list[Candle] = []
        self._evaluation_lock = asyncio.Lock()
        self._last_eval_time: datetime = datetime.min.replace(tzinfo=timezone.utc)  # epoch → forces first-call refresh
        self._last_cycle_status: str | None = None
        self._cycle_count: int = 0
        self._last_heartbeat_log_time: datetime = datetime.min.replace(tzinfo=timezone.utc)
        self._last_eod_report_date: Optional[str] = None
        self._market_data_status: dict[str, Any] = {
            "provider": "unknown", "provider_active": False,
            "futures_instrument": None, "futures_candle_count": 0,
            "latest_futures_candle": None, "last_error": "Awaiting first evaluation",
        }
        self._startup_reconciliation: dict[str, Any] = {
            "status": "NOT_CHECKED",
            "issues": [],
        }
        self._last_live_reconciliation: dict[str, Any] = {
            "status": "NOT_CHECKED",
            "issues": [],
        }
        self.strategy_a_telemetry = StrategyATelemetryStore()


    @staticmethod
    def _normalized_option_right(value: object) -> str:
        text = str(value or "").upper()
        if text in {"CALL", "CE"}:
            return "CALL"
        if text in {"PUT", "PE"}:
            return "PUT"
        return text

    def _broker_position_matches_trade(
        self,
        position: Any,
        trade: ActiveTrade,
    ) -> bool:
        broker_symbol = str(
            getattr(position, "stock_code", "") or ""
        ).upper()
        if broker_symbol == trade.contract_symbol.upper():
            return True

        provider = self._active_broker_context()[0]
        if provider != "breeze":
            return False

        expiry = str(getattr(position, "expiry_date", "") or "")[:10]
        strike = getattr(position, "strike_price", None)
        right = self._normalized_option_right(
            getattr(position, "right", None)
        )
        underlying = (
            "BANKNIFTY"
            if trade.contract_symbol.upper().startswith("BANKNIFTY")
            else "NIFTY"
        )
        return bool(
            broker_symbol == underlying
            and expiry == str(trade.expiry)[:10]
            and strike is not None
            and abs(float(strike) - float(trade.strike)) < 1e-6
            and right == trade.option_type.value
        )

    @staticmethod
    def _live_trade_contract_key(trade: ActiveTrade) -> str:
        """Return the canonical local identity used for broker net-position reconciliation."""
        instrument_id = str(trade.contract_instrument_id or "").strip().upper()
        if instrument_id:
            return f"INSTRUMENT:{instrument_id}"
        return (
            "CONTRACT:"
            f"{trade.contract_symbol.upper()}|"
            f"{str(trade.expiry)[:10]}|"
            f"{float(trade.strike):.8f}|"
            f"{trade.option_type.value}"
        )

    @staticmethod
    def _local_live_remaining_quantity(trade: ActiveTrade) -> int:
        """Return locally confirmed broker exposure, not merely requested entry size."""
        base_quantity = (
            int(trade.filled_quantity or 0)
            if trade.state == TradeLifecycleState.ENTRY_PENDING
            else int(trade.quantity or 0)
        )
        return max(
            0,
            base_quantity - int(trade.exit_filled_quantity or 0),
        )

    async def build_live_reconciliation_report(
        self,
        *,
        broker_positions: list[Any],
        orders: list[Any],
        broker_verified: bool,
        broker_error: str | None = None,
        record_as_startup: bool = False,
    ) -> dict[str, Any]:
        """Compare durable local LIVE state with broker net positions by contract."""
        active_trades = await self.repo.get_active_trades()
        live_trades = [
            trade
            for trade in active_trades
            if trade.mode == AutoTradingMode.LIVE
            and trade.state != TradeLifecycleState.CLOSED
        ]
        unresolved_states = {
            "CREATED",
            "VALIDATING",
            "APPROVED",
            "SUBMITTING",
            "SUBMISSION_UNKNOWN",
            "ACKNOWLEDGED",
            "OPEN",
            "PARTIALLY_FILLED",
        }
        unresolved_orders = [
            order
            for order in orders
            if order.trading_mode == TradingMode.LIVE
            and order.status.value in unresolved_states
        ]
        issues: list[str] = []
        matched_position_indexes: set[int] = set()
        trade_rows: list[dict[str, Any]] = []
        contract_rows: list[dict[str, Any]] = []

        if not broker_verified:
            issues.append(
                "BROKER_STATE_UNVERIFIED"
                + (f":{broker_error}" if broker_error else "")
            )

        # Broker APIs expose a net position per option contract. Multiple local
        # strategy trades in that contract are fungible at the broker, so
        # reconcile the aggregate local exposure against the aggregate broker
        # exposure exactly once rather than comparing the same broker quantity
        # independently with every local trade.
        contract_groups: dict[str, list[ActiveTrade]] = {}
        for trade in live_trades:
            contract_groups.setdefault(
                self._live_trade_contract_key(trade),
                [],
            ).append(trade)

        positions_by_contract: dict[str, list[tuple[int, Any]]] = {
            key: [] for key in contract_groups
        }
        for index, position in enumerate(broker_positions):
            matching_keys = [
                key
                for key, grouped_trades in contract_groups.items()
                if any(
                    self._broker_position_matches_trade(position, trade)
                    for trade in grouped_trades
                )
            ]
            if len(matching_keys) == 1:
                key = matching_keys[0]
                positions_by_contract[key].append((index, position))
                matched_position_indexes.add(index)
            elif len(matching_keys) > 1:
                # Do not allocate one broker net position across ambiguous local
                # identities. Mark it seen to avoid a misleading orphan issue,
                # then fail closed with the ambiguity itself.
                matched_position_indexes.add(index)
                issues.append(
                    "AMBIGUOUS_BROKER_POSITION_MATCH:"
                    f"{getattr(position, 'stock_code', 'UNKNOWN')}:"
                    + ",".join(sorted(matching_keys))
                )

        for contract_key, grouped_trades in contract_groups.items():
            local_contract_quantity = sum(
                self._local_live_remaining_quantity(trade)
                for trade in grouped_trades
            )
            matches = positions_by_contract.get(contract_key, [])
            broker_contract_quantity = sum(
                int(getattr(position, "quantity", 0) or 0)
                for _, position in matches
            )
            trade_ids = [trade.trade_id for trade in grouped_trades]

            if (
                broker_verified
                and broker_contract_quantity != local_contract_quantity
            ):
                issues.append(
                    f"LIVE_POSITION_MISMATCH:{contract_key}:"
                    f"local={local_contract_quantity}:"
                    f"broker={broker_contract_quantity}:"
                    f"trades={','.join(trade_ids)}"
                )

            contract_rows.append(
                {
                    "contract_key": contract_key,
                    "symbol": grouped_trades[0].contract_symbol,
                    "trade_ids": trade_ids,
                    "local_remaining_quantity": local_contract_quantity,
                    "broker_quantity": broker_contract_quantity,
                    "broker_position_count": len(matches),
                }
            )

            for trade in grouped_trades:
                remaining = self._local_live_remaining_quantity(trade)
                protection_status = "NOT_REQUIRED"
                if (
                    remaining > 0
                    and not trade.exit_order_id
                    and not trade.partial_exit_order_id
                ):
                    if not trade.protective_stop_order_id:
                        protection_status = "MISSING"
                        issues.append(
                            f"LIVE_PROTECTIVE_STOP_MISSING:{trade.trade_id}"
                        )
                    else:
                        protective = await self.oms.get_order(
                            trade.protective_stop_order_id
                        )
                        if protective is None:
                            protection_status = "OMS_ORDER_MISSING"
                            issues.append(
                                f"LIVE_PROTECTIVE_STOP_UNKNOWN:{trade.trade_id}"
                            )
                        else:
                            protection_status = protective.status.value
                            if protective.status.value not in {
                                "OPEN",
                                "ACKNOWLEDGED",
                                "PARTIALLY_FILLED",
                            }:
                                issues.append(
                                    "LIVE_PROTECTIVE_STOP_NOT_CONFIRMED:"
                                    f"{trade.trade_id}:"
                                    f"{protective.status.value}"
                                )

                if remaining > 0:
                    issues.append(
                        f"RECOVERED_LIVE_POSITION_ACTIVE:{trade.trade_id}"
                    )

                trade_rows.append(
                    {
                        "trade_id": trade.trade_id,
                        "symbol": trade.contract_symbol,
                        "contract_key": contract_key,
                        "local_remaining_quantity": remaining,
                        # Kept for response compatibility. This is deliberately
                        # the broker's aggregate contract quantity, not a
                        # fabricated per-trade allocation.
                        "broker_quantity": broker_contract_quantity,
                        "broker_contract_quantity": broker_contract_quantity,
                        "local_contract_quantity": local_contract_quantity,
                        "contract_trade_count": len(grouped_trades),
                        "protective_stop_status": protection_status,
                        "exit_order_id": trade.exit_order_id,
                        "partial_exit_order_id": trade.partial_exit_order_id,
                    }
                )

        for index, position in enumerate(broker_positions):
            quantity = int(getattr(position, "quantity", 0) or 0)
            if quantity == 0:
                continue
            if index not in matched_position_indexes:
                issues.append(
                    "ORPHAN_BROKER_POSITION:"
                    f"{getattr(position, 'stock_code', 'UNKNOWN')}:"
                    f"{quantity}"
                )
            if quantity < 0:
                issues.append(
                    "UNEXPECTED_SHORT_BROKER_POSITION:"
                    f"{getattr(position, 'stock_code', 'UNKNOWN')}:"
                    f"{quantity}"
                )

        if unresolved_orders:
            issues.append("UNRESOLVED_LIVE_ORDERS")

        report = {
            "status": "BLOCKED" if issues else "CLEAN",
            "checked_at": utc_now().isoformat(),
            "broker_verified": broker_verified,
            "broker_error": broker_error,
            "issues": list(dict.fromkeys(issues)),
            "active_live_trades": trade_rows,
            "live_position_contracts": contract_rows,
            "open_broker_positions": sum(
                1
                for position in broker_positions
                if int(getattr(position, "quantity", 0) or 0) != 0
            ),
            "unresolved_live_orders": [
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "status": order.status.value,
                    "reduce_only": order.reduce_only,
                }
                for order in unresolved_orders
            ],
        }
        self._last_live_reconciliation = report
        if record_as_startup:
            self._startup_reconciliation = report
        return report

    def _reset_setups(self, at):
        self.strategy_a.reset(at)
        self.strategy_b.reset(at)

    async def _reject_strategy_a_execution(self, signal: StrategySignal, reason: str) -> None:
        """Recover a triggered Strategy A setup when downstream execution rejects."""
        if not self._is_strategy_a(signal.strategy):
            return
        self.strategy_a.on_execution_rejected(signal.timestamp, reason)
        await self._save_runtime()

    async def _persist_strategy_a_telemetry(self, record: StrategyAEvaluationRecord) -> None:
        self.strategy_a_telemetry.append(record)
        entry = DecisionLogEntry(
            id=f"TEL-A-{generate_id()}",
            timestamp=datetime.fromisoformat(record.timestamp),
            category="STRATEGY_A_TELEMETRY",
            strategy=StrategyName.TREND_PULLBACK.value,
            message=record.management_event or "EVALUATED",
            details=record.model_dump(mode="json"),
        )
        result = self.repo.save_decision_log(entry)
        if inspect.isawaitable(result):
            await result

    async def _record_strategy_a_evaluation(self, features: MarketFeatures, signal: StrategySignal | None) -> None:
        futures = self._market_snapshot[2]
        if not futures:
            return
        snapshot = signal.features_snapshot if signal else {}
        candle_timestamp = snapshot.get("completed_candle_timestamp") or futures[-1].end_time.isoformat()
        contract = snapshot.get("futures_contract") or futures[-1].instrument_id
        await self._persist_strategy_a_telemetry(StrategyAEvaluationRecord(
            timestamp=features.timestamp.isoformat(),
            futures_contract=contract,
            completed_candle_timestamp=candle_timestamp,
            ema20=snapshot.get("ema20", features.ema20_15m), ema50=snapshot.get("ema50", features.ema50_15m),
            adx=snapshot.get("adx14", features.adx_15m), plus_di=snapshot.get("plus_di14", features.plus_di_15m),
            minus_di=snapshot.get("minus_di14", features.minus_di_15m), atr=snapshot.get("atr14", features.atr_15m),
            vwap=snapshot.get("session_vwap", features.futures_vwap), active_support=snapshot.get("support"),
            active_resistance=snapshot.get("resistance"), trend_result=snapshot.get("trend"),
            confluence_result=snapshot.get("confluence_result"), confirmation_result=snapshot.get("confirmation_result"),
            trigger=snapshot.get("trigger"), structural_stop=signal.structural_stop if signal else None,
            underlying_r=signal.r_points if signal else None, strategy_state=self.strategy_a.snapshot.state.value,
            rejection_or_invalidation_reason=self.strategy_a.last_event.reason if self.strategy_a.last_event else None,
            entry_fill=(signal.underlying_entry_price if signal else snapshot.get("entry_price")), management_event=self.strategy_a.last_event.event if self.strategy_a.last_event else "EVALUATED",
        ))

    async def _record_strategy_a_lifecycle_event(self, trade: ActiveTrade, features: MarketFeatures, event: str, reason: str | None = None) -> None:
        futures = self._market_snapshot[2]
        record = StrategyAEvaluationRecord(
            timestamp=features.timestamp.isoformat(),
            futures_contract=trade.futures_contract_id or (futures[-1].instrument_id if futures else "UNAVAILABLE"),
            completed_candle_timestamp=(futures[-1].end_time.isoformat() if futures else features.timestamp.isoformat()),
            ema20=features.ema20_15m, ema50=features.ema50_15m, adx=features.adx_15m,
            plus_di=features.plus_di_15m, minus_di=features.minus_di_15m, atr=features.atr_15m,
            vwap=features.futures_vwap, option_contract=trade.contract_instrument_id,
            expiry=trade.expiry, quote_timestamp=(trade.current_quote_timestamp.isoformat() if trade.current_quote_timestamp else None),
            quote_freshness_seconds=trade.current_quote_freshness_seconds,
            delta=trade.selected_option_delta, gamma=trade.selected_option_gamma,
            delta_source=trade.selected_option_delta_source, gamma_source=trade.selected_option_gamma_source,
            bid=trade.current_bid, ask=trade.current_ask,
            spread=(trade.current_ask - trade.current_bid if trade.current_ask and trade.current_bid else None),
            position_size=trade.quantity, lot_size=trade.lot_size, lots=trade.lots,
            risk_budget=trade.risk_budget,
            estimated_option_loss_at_structural_stop=trade.estimated_option_loss_at_structural_stop,
            entry_fill=trade.underlying_entry_price, option_entry_fill=trade.entry_executable_price,
            slippage_points=(
                trade.partial_exit_slippage_points if event == "PARTIAL_EXIT"
                else trade.entry_slippage_points if event in ("POSITION_SIZED", "ENTRY_OPENED")
                else (trade.slippage_cost / max(1, (trade.initial_quantity or trade.quantity) + trade.partial_exit_filled_quantity + (trade.final_exit_quantity or 0)) if event == "CLOSED" and trade.slippage_cost is not None else trade.slippage_cost)
            ),
            partial_exit_quantity=trade.partial_exit_filled_quantity,
            final_exit_quantity=trade.final_exit_quantity,
            transaction_costs=trade.transaction_costs,
            execution_order_count=trade.execution_order_count,
            structural_stop=trade.underlying_structural_stop,
            underlying_r=None if trade.underlying_outcome_status == OPTION_EMERGENCY_STOP_OUTCOME_STATUS else trade.current_r,
            strategy_state=trade.state.value, rejection_or_invalidation_reason=reason,
            management_event=event, exit_reason=trade.exit_reason,
            underlying_outcome_status=trade.underlying_outcome_status,
            realized_r=trade.realized_r,
            option_pnl=trade.net_pnl,
        )
        await self._persist_strategy_a_telemetry(record)

    def get_strategy_a_telemetry_summary(self) -> dict[str, Any]:
        return self.strategy_a_telemetry.summary()

    async def _save_runtime(self):
        await self.repo.save_runtime(self.strategy_a.export_state())
        await self.repo.save_runtime(self.strategy_b.export_state(), "volatility_breakout")
        await self.repo.save_runtime(self.strategy_e.export_state(), "pivot_vwap_scalp")

    async def initialize(self) -> None:
        await self.repo.initialize()
        self.config = await self.repo.get_auto_config()
        # Arming is deliberately process-local in effect. A restart must never
        # silently restore authority to create new LIVE exposure.
        if self.config.system_armed:
            self.config.system_armed = False
            await self.repo.save_auto_config(self.config)
        self._sync_subcomponents()
        try:
            await self.strategy_c_shadow.initialize()
        except Exception:
            logger.exception("Strategy C shadow initialization failed; Strategy A/B remain unaffected")
            self._last_strategy_c_shadow_status = {"status": "INITIALIZATION_FAILED"}
        try:
            await self.strategy_d_paper.initialize()
        except Exception:
            logger.exception("Strategy D paper initialization failed; Strategy A/B/C remain unaffected")
            self._last_strategy_d_paper_status = {"status": "INITIALIZATION_FAILED"}
        # Telemetry is an audit stream, not process-local state.  Restore the
        # persisted Strategy A records before the scheduler can emit a new
        # evaluation, so summaries survive a service restart.
        try:
            persisted_logs = await self.repo.list_decision_logs(limit=10000)
            for log in reversed(persisted_logs):
                if log.category != "STRATEGY_A_TELEMETRY":
                    continue
                try:
                    self.strategy_a_telemetry.append(StrategyAEvaluationRecord.model_validate(log.details))
                except Exception:
                    logger.warning("Ignoring malformed persisted Strategy A telemetry log %s", log.id)
        except Exception:
            logger.exception("Unable to restore persisted Strategy A telemetry")
        self.strategy_a.restore_state(await self.repo.get_runtime())
        self.strategy_b.restore_state(await self.repo.get_runtime("volatility_breakout"))
        self.strategy_e.restore_state(await self.repo.get_runtime("pivot_vwap_scalp"))
        self._active_trades_cache = await self.repo.get_active_trades()

        # Repair the only two crash-consistency mismatches permitted by older
        # builds: phantom ENTERED without a trade, or an ARMED signal whose
        # trade was persisted immediately before runtime-state confirmation.
        strategy_a_trades = [
            trade for trade in self._active_trades_cache
            if self._is_strategy_a(trade.strategy)
        ]
        runtime_reconciled = False
        if self.strategy_a.snapshot.state is StrategyState.ENTERED and not strategy_a_trades:
            self.strategy_a.on_execution_rejected(
                utc_now(),
                "RECOVERED_PHANTOM_ENTERED_STATE",
            )
            runtime_reconciled = True
        elif self.strategy_a.snapshot.state is StrategyState.ARMED and strategy_a_trades:
            self.strategy_a.confirm_entry(min(trade.entry_time for trade in strategy_a_trades))
            runtime_reconciled = True
        if runtime_reconciled:
            await self._save_runtime()

        await self._seed_default_strategy()

        # Start background evaluation loop if enabled
        if not self._loop_task or self._loop_task.done():
            self._is_running = True
            self._loop_task = asyncio.create_task(self._run_scheduler_loop())

    def _sync_subcomponents(self) -> None:
        self.position_manager = PositionManager(self.config.risk, self.config.session, strategy_config=self.config.tunables)
        self.risk_sizer = UnderlyingRiskSizer(self.config.risk)
        self.contract_selector = ContractSelector(self.config.option_selection)
        self.strategy_c_shadow.refresh_dependencies(
            contract_selector=self.contract_selector,
            risk_sizer=self.risk_sizer,
            risk_config=self.config.risk,
        )
        self.strategy_d_paper.refresh_dependencies(
            contract_selector=self.contract_selector,
            risk_config=self.config.risk,
            session_config=self.config.session,
        )
        self.strategy_a = TrendPullbackStrategy(config=self.config.tunables)
        self.strategy_e = PivotVwapScalpStrategy(self.config.tunables)
        self.strategy_b = VolatilityBreakoutStrategy(
            rvol_threshold=self.config.tunables.rvol_threshold,
            adx_threshold=self.config.tunables.strategy_b_adx_threshold,
            min_confirmation_score=self.config.tunables.strat_b_min_confirmation,
            box_max_height_atr=self.config.tunables.box_max_height_atr,
            bb_width_percentile_threshold=self.config.tunables.bb_width_percentile_threshold,
            lookback_bars=self.config.tunables.compression_lookback_bars,
            max_age_bars=self.config.tunables.box_max_age_bars,
            breakout_buffer_atr=self.config.tunables.breakout_buffer_atr,
            max_extension_atr=self.config.tunables.breakout_max_extension_atr,
            entry_start=self.config.session.strategy_b_no_new_trade_before,
            entry_end=self.config.session.no_new_trade_after,
        )
        self.simulation_engine = SimulationEngine(
            historical_service=self.hist_svc,
            risk_config=self.config.risk,
            session_config=self.config.session,
            tunables=self.config.tunables,
        )

    @staticmethod
    def _is_strategy_a(strategy: StrategyName) -> bool:
        return strategy == StrategyName.TREND_PULLBACK

    @staticmethod
    def _is_strategy_e(strategy: StrategyName) -> bool:
        return strategy == StrategyName.PIVOT_VWAP_SCALP

    @staticmethod
    def _is_candidate_execution_strategy(strategy: StrategyName) -> bool:
        return strategy in {
            StrategyName.DI_CONTINUATION,
            StrategyName.SR_MOMENTUM_BREAKOUT,
        }

    @staticmethod
    def _uses_delta_aware_selector(strategy: StrategyName) -> bool:
        return strategy in {
            StrategyName.TREND_PULLBACK,
            StrategyName.DI_CONTINUATION,
        }

    @staticmethod
    def _uses_underlying_risk_sizing(strategy: StrategyName) -> bool:
        return strategy in {
            StrategyName.TREND_PULLBACK,
            StrategyName.DI_CONTINUATION,
        }

    def _execution_policy_for_strategy(self, strategy: StrategyName):
        """Return the single authoritative A/B/C/D/E execution policy."""
        return resolve_strategy_execution_policy(
            strategy,
            self.config.mode,
            strategy_a_option_execution_ready=bool(
                self._market_data_status.get(
                    "strategy_a_option_execution_ready",
                    False,
                )
            ),
            strategy_a_option_execution_reason=(
                self._market_data_status.get(
                    "strategy_a_option_execution_reason"
                )
            ),
        )

    def _execution_policy_matrix(self) -> dict[str, dict[str, Any]]:
        return {
            strategy.value: self._execution_policy_for_strategy(strategy).to_dict()
            for strategy in StrategyName
        }

    def _execution_mode_for_strategy(
        self,
        strategy: StrategyName,
        option_type: OptionType,
    ) -> AutoTradingMode:
        """Resolve execution mode from the authoritative permission policy."""
        return self._execution_policy_for_strategy(strategy).mode_for(option_type)

    def _execution_mode_for_signal(self, signal: StrategySignal) -> AutoTradingMode:
        """Resolve signal execution under the authoritative per-strategy policy."""
        return self._execution_mode_for_strategy(signal.strategy, signal.option_type)

    def _paper_slippage(self) -> float:
        return float(self.config.risk.paper_slippage_points)

    @staticmethod
    def _live_orders_enabled() -> bool:
        try:
            return bool(get_platform_settings().live_trading_enabled)
        except Exception:
            return False

    def _cost_metadata(self) -> dict[str, Any]:
        r = self.config.risk
        return {
            "version": r.paper_cost_assumption_version,
            "brokerage_per_order": r.paper_brokerage_per_order,
            "exchange_charge_rate": r.paper_exchange_charge_rate,
            "stt_sell_rate": r.paper_stt_sell_rate,
            "gst_rate": r.paper_gst_rate,
            "sebi_charge_rate": r.paper_sebi_charge_rate,
            "stamp_buy_rate": r.paper_stamp_buy_rate,
            "slippage_points": self._paper_slippage(),
        }

    async def _record_option_quote(self, trade: ActiveTrade, quote: dict[str, Any]) -> None:
        """Persist every selected-contract quote, including unusable samples."""
        quote = dict(quote)
        quote.setdefault("trade_id", trade.trade_id)
        quote.setdefault("strategy_signal_id", trade.signal_id)
        quote.setdefault("instrument_id", trade.contract_instrument_id)
        quote.setdefault("quote_timestamp", utc_now().isoformat())
        try:
            await self.repo.save_option_quote(quote)
        except Exception:
            logger.exception("Option quote audit persistence failed")

    async def _record_execution(self, entry: dict[str, Any]) -> None:
        try:
            await self.repo.save_execution_ledger(entry)
        except Exception:
            logger.exception("Option execution audit persistence failed")

    async def _resolve_option_quote(self, trade: ActiveTrade) -> dict[str, Any]:
        """Resolve only a real, fresh quote for the immutable selected contract.

        There is deliberately no spot/delta or entry-price fallback here.  A
        missing quote is an incomplete validation observation, never a fill.
        """
        now = utc_now()
        reasons: list[str] = []
        unusable_market_quote: dict[str, Any] | None = None
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote(trade.contract_instrument_id)
            if q:
                freshness = max(0.0, (now - q.timestamp).total_seconds())
                quote = {
                    "quote_timestamp": q.timestamp.isoformat(),
                    "source": getattr(q, "source", "UNKNOWN"),
                    "freshness_seconds": freshness,
                    "bid": float(getattr(q, "best_bid", 0) or 0),
                    "ask": float(getattr(q, "best_ask", 0) or 0),
                    "ltp": float(getattr(q, "last_price", 0) or 0),
                    "volume": int(getattr(q, "volume", 0) or 0),
                    "open_interest": int(getattr(q, "open_interest", 0) or 0),
                }
                if quote["source"] in ("BREEZE", "KITE", "LIVE") and freshness <= 30:
                    if quote["bid"] <= 0:
                        reasons.append("missing bid")
                    if quote["ask"] <= 0:
                        reasons.append("missing ask")
                    if quote["ask"] > 0 and quote["bid"] > quote["ask"]:
                        reasons.append("zero/invalid spread")
                    if reasons:
                        unusable_market_quote = {**quote, "status": "INVALID", "reason": "; ".join(dict.fromkeys(reasons))}
                    # Preserve the pre-existing live reconciliation path,
                    # which only requires a broker bid for a pending sell.
                    if trade.mode == AutoTradingMode.LIVE and quote["bid"] > 0 and (quote["ask"] <= 0 or quote["bid"] <= quote["ask"]):
                        quote["status"] = "VALID"
                        await self._record_option_quote(trade, quote)
                        return quote
                    if not reasons:
                        quote["status"] = "VALID"
                        await self._record_option_quote(trade, quote)
                        return quote
                elif quote["source"] in ("BREEZE", "KITE", "LIVE"):
                    reasons.append("stale quote")
                else:
                    reasons.append("API error")

        if self.chain_svc:
            try:
                chain = await self.chain_svc.get_chain(underlying="NIFTY", expiry=trade.expiry)
                source = chain.get("source", "UNKNOWN")
                captured_at = chain.get("captured_at") or now.isoformat()
                captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00")) if isinstance(captured_at, str) else now
                freshness = max(0.0, (now - captured_dt).total_seconds())
                for strike in chain.get("strikes", []):
                    for side in ("call", "put"):
                        leg = strike.get(side) or {}
                        if leg.get("instrument_id") != trade.contract_instrument_id and leg.get("symbol") != trade.contract_symbol:
                            continue
                        quote = {
                            "quote_timestamp": captured_dt.isoformat(),
                            "source": source,
                            "freshness_seconds": freshness,
                            "bid": float(leg.get("bid") or 0),
                            "ask": float(leg.get("ask") or 0),
                            "ltp": float(leg.get("ltp") or 0),
                            "volume": int(leg.get("volume") or 0),
                            "open_interest": int(leg.get("open_interest") or 0),
                        }
                        if source not in ("BREEZE", "KITE", "LIVE"):
                            reasons.append("API error: non-live/synthetic option quote")
                        elif freshness > 30:
                            reasons.append("stale quote")
                        elif quote["bid"] <= 0:
                            reasons.append("missing bid")
                        elif quote["ask"] <= 0:
                            reasons.append("missing ask")
                        elif quote["bid"] > quote["ask"]:
                            reasons.append("zero/invalid spread")
                        elif quote["ltp"] <= 0:
                            reasons.append("option quote unavailable after entry")
                        else:
                            quote["status"] = "VALID"
                            await self._record_option_quote(trade, quote)
                            return quote
                        await self._record_option_quote(trade, {**quote, "status": "INVALID", "reason": reasons[-1]})
                        break
            except Exception as exc:
                reasons.append(f"API error: {type(exc).__name__}")

        if not reasons:
            reasons.append("option quote unavailable after entry")
        if unusable_market_quote is not None:
            await self._record_option_quote(trade, unusable_market_quote)
            return unusable_market_quote
        quote = {
            "quote_timestamp": now.isoformat(), "source": "UNAVAILABLE", "freshness_seconds": None,
            "bid": None, "ask": None, "ltp": None, "volume": None, "open_interest": None,
            "status": "UNAVAILABLE", "reason": "; ".join(dict.fromkeys(reasons)),
        }
        await self._record_option_quote(trade, quote)
        return quote

    def _apply_quote_to_trade(self, trade: ActiveTrade, quote: dict[str, Any]) -> None:
        trade.current_bid = quote.get("bid")
        trade.current_ask = quote.get("ask")
        trade.current_ltp = quote.get("ltp")
        trade.current_quote_source = quote.get("source")
        ts = quote.get("quote_timestamp")
        trade.current_quote_timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else None
        trade.current_quote_freshness_seconds = quote.get("freshness_seconds")
        trade.current_quote_volume = quote.get("volume")
        trade.current_quote_open_interest = quote.get("open_interest")
        trade.option_data_status = quote.get("status", "UNAVAILABLE")
        reason = quote.get("reason")
        if reason and reason not in trade.option_data_quality_reasons:
            trade.option_data_quality_reasons.append(reason)

    async def _seed_default_strategy(self) -> None:
        # 1. Default EMA Breakout Strategy (for test & manual signal compatibility)
        ema_def_id = "DEF-EMA-OPTIONS-V1"
        await self.repo.save_definition(
            definition_id=ema_def_id,
            name="EMA Breakout Options Strategy",
            version="1.0.0",
            description="5-min EMA 9/21 cross strategy for NIFTY ATM call and put options",
        )
        await self.repo.save_instance(
            instance_id="INST-NIFTY-EMA-PAPER",
            definition_id=ema_def_id,
            name="NIFTY EMA Paper Runner",
            mode=TradingMode.PAPER,
            symbol="NIFTY",
            parameters={"fast_period": 9, "slow_period": 21, "lot_multiplier": 1},
            status="RUNNING",
        )

        # 2. Advanced NIFTY Auto-Trading Engine
        def_id = "DEF-NIFTY-AUTO-V1"
        await self.repo.save_definition(
            definition_id=def_id,
            name="NIFTY Intraday Options Auto Trading Engine",
            version="1.0.0",
            description="Multi-strategy algorithmic trading for NIFTY intraday options under max ₹70 premium cap.",
        )
        await self.repo.save_instance(
            instance_id="INST-NIFTY-AUTO-ENGINE",
            definition_id=def_id,
            name="NIFTY Intraday Options Orchestrator",
            mode=(
                TradingMode.LIVE
                if self.config.mode == AutoTradingMode.LIVE
                else TradingMode.PAPER
            ),
            symbol="NIFTY",
            parameters=self.config.model_dump(),
            status="RUNNING",
        )

    # --- Configuration Management ---
    async def get_config(self) -> AutoTradingConfig:
        return self.config

    async def set_execution_mode(
        self,
        mode: AutoTradingMode,
    ) -> AutoTradingConfig:
        """Switch PAPER/SHADOW/LIVE authority while forcing the system disarmed."""
        if mode == AutoTradingMode.DISABLED:
            raise ValueError("DISABLED is not available from the trading-mode switch.")

        active_trades = await self.repo.get_active_trades()
        if active_trades and mode != self.config.mode:
            raise ValueError(
                "Cannot switch trading mode while positions are active."
            )

        if self.config.system_armed:
            await self.arm_system(False)

        updated = self.config.model_copy(
            update={
                "mode": mode,
                "system_armed": False,
            }
        )
        return await self.update_config(updated)

    async def update_config(self, new_config: AutoTradingConfig) -> AutoTradingConfig:
        # Mode transitions with active positions are never safe because the
        # lifecycle authority must remain stable until those positions close.
        if self._active_trades_cache and new_config.mode != self.config.mode:
            raise ValueError("Cannot switch trading mode while positions are active.")
        if (
            new_config.mode == AutoTradingMode.LIVE
            and self.config.mode != AutoTradingMode.LIVE
            and (self.config.system_armed or new_config.system_armed)
        ):
            raise ValueError(
                "System must be DISARMED before switching strategy mode to LIVE."
            )

        runtime = self.strategy_a.export_state()
        self.config = new_config
        self._sync_subcomponents()
        self.strategy_a.restore_state(runtime)
        self.strategy_b.reset(utc_now())
        await self._save_runtime()
        await self.repo.save_auto_config(new_config)

        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message="Auto-trading configuration updated",
            details=new_config.model_dump(mode="json"),
        )
        return self.config

    async def arm_system(self, armed: bool) -> AutoTradingConfig:
        self.config.system_armed = armed
        await self.repo.save_auto_config(self.config)
        await self._log_decision(
            category="SECURITY",
            strategy="SYSTEM",
            message=f"System live trading {'ARMED' if armed else 'DISARMED'}",
            details={"armed": armed, "mode": self.config.mode.value},
        )
        return self.config

    async def set_auto_trade(self, enabled: bool) -> AutoTradingConfig:
        self.config.auto_trade_enabled = enabled
        await self.repo.save_auto_config(self.config)
        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message=f"Auto-trading execution {'ENABLED' if enabled else 'DISABLED'}",
            details={"auto_trade_enabled": enabled},
        )
        return self.config

    async def toggle_kill_switch(self, active: bool) -> AutoTradingConfig:
        self.config.kill_switch = active
        if active:
            self.config.auto_trade_enabled = False
            self.config.system_armed = False
        await self.repo.save_auto_config(self.config)
        await self._log_decision(
            category="EMERGENCY",
            strategy="SYSTEM",
            message=f"Emergency Kill Switch {'ACTIVATED' if active else 'DEACTIVATED'}",
            details={"kill_switch": active},
        )
        return self.config

    async def _run_scheduler_loop(self) -> None:
        logger.info("Strategy evaluation scheduler loop started.")
        while self._is_running:
            try:
                interval = max(1, self.config.tunables.evaluation_interval_sec)
                cycle_result = await self.evaluate_cycle()
                self._cycle_count += 1
                self._last_cycle_status = str(cycle_result.get("status", "UNKNOWN"))
                heartbeat_now = utc_now()
                if (heartbeat_now - self._last_heartbeat_log_time).total_seconds() >= 60:
                    self._last_heartbeat_log_time = heartbeat_now
                    logger.info(
                        "Strategy heartbeat: cycles=%d last_status=%s provider=%s active=%s futures=%s latest_futures=%s",
                        self._cycle_count,
                        self._last_cycle_status,
                        self._market_data_status.get("provider"),
                        self._market_data_status.get("provider_active"),
                        self._market_data_status.get("futures_instrument"),
                        self._market_data_status.get("latest_futures_candle"),
                    )
                ist_now = heartbeat_now.astimezone(timezone(timedelta(hours=5, minutes=30)))
                force_exit = datetime.strptime(self.config.session.force_exit_time, "%H:%M").time()
                session_date = ist_now.date().isoformat()
                if ist_now.time() >= force_exit and self._last_eod_report_date != session_date:
                    await self.generate_eod_report(session_date)
                    self._last_eod_report_date = session_date
                # Broadcast live status update to UI over event bus
                st = await self.get_status()
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.STRATEGY_SIGNAL,
                        payload={"event": "STATUS_UPDATE", "data": st},
                    )
                )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error("Error in strategy evaluation loop: %s", ex, exc_info=True)
                await asyncio.sleep(2)

    async def stop(self) -> None:
        self._is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    # --- Core Cycle Evaluation ---
    async def evaluate_cycle(self) -> dict[str, Any]:
        async with self._evaluation_lock:
            return await self._evaluate_cycle()

    async def _evaluate_cycle(self) -> dict[str, Any]:
        """Executes a single evaluation cycle: features -> active trade management -> entry signals."""
        now = utc_now()
        self._last_eval_time = now  # track for get_status() freshness check

        # 1. Gather market features. Existing positions must continue to be
        # managed even when the entry kill switch is active.
        features = await self._gather_features()
        self._last_features = features

        # Passive Strategy C research observation. This sidecar never creates
        # ActiveTrade state or OMS intents and cannot affect Strategy A/B gates.
        try:
            self._last_strategy_c_shadow_status = await self.strategy_c_shadow.observe(
                active_futures_instrument=self._market_data_status.get("futures_instrument"),
                now=now,
            )
        except Exception:
            logger.exception("Strategy C shadow observation failed; Strategy A/B evaluation continues")
            self._last_strategy_c_shadow_status = {"status": "OBSERVATION_FAILED"}

        # Strategy D is a frozen paper sidecar. It runs on the same scheduler
        # heartbeat but remains isolated from OMS/live routing until promoted.
        try:
            self._last_strategy_d_paper_status = await self.strategy_d_paper.observe(
                active_futures_instrument=self._market_data_status.get("futures_instrument"),
                now=now,
            )
        except Exception:
            logger.exception("Strategy D paper observation failed; Strategy A/B/C evaluation continues")
            self._last_strategy_d_paper_status = {"status": "OBSERVATION_FAILED"}

        # 3. Manage active trades (trailing stops, thesis reversal, square-off)
        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades

        for trade in active_trades:
            await self._evaluate_active_trade(trade, features)

        self._active_trades_cache = await self.repo.get_active_trades()

        # Loss of execution-grade broker/feed health while armed is a
        # fail-closed event. Existing positions were already managed above and
        # broker-held catastrophe stops remain active; only new authority is
        # revoked, requiring an explicit operator re-arm after recovery.
        if (
            self.config.mode == AutoTradingMode.LIVE
            and self.config.system_armed
            and not bool(
                self._market_data_status.get(
                    "execution_feed_healthy",
                    False,
                )
            )
        ):
            self.config.system_armed = False
            await self.repo.save_auto_config(self.config)
            self._reset_setups(now)
            await self._save_runtime()
            reasons = list(
                self._market_data_status.get(
                    "execution_feed_reasons",
                    [],
                )
            )
            await self._log_decision(
                category="SECURITY",
                strategy="SYSTEM",
                message=(
                    "LIVE system auto-disarmed after broker/feed health loss"
                ),
                details={
                    "reasons": reasons,
                    "active_positions_managed": len(
                        self._active_trades_cache
                    ),
                },
            )
            return {
                "status": "LIVE_RUNTIME_HEALTH_AUTO_DISARMED",
                "reasons": reasons,
                "active_positions_managed": len(
                    self._active_trades_cache
                ),
            }

        # The strategy kill switch is entry-blocking, not exit-blocking.
        # Existing positions have already had stops/session exits evaluated
        # above, so it is now safe to stop before any new entry logic.
        if self.config.kill_switch:
            self._reset_setups(now)
            await self._save_runtime()
            return {
                "status": "HALTED_KILL_SWITCH",
                "active_positions_managed": len(self._active_trades_cache),
            }

        bypass = getattr(self._active_overrides, "bypass_entry_window", False)
        _, _, futures_candles = self._market_snapshot
        strategy_a_data_ready = self.config.tunables.trend_pullback_enabled and bool(futures_candles)
        candidate_entry_available = bool(
            (
                self.config.tunables.di_continuation_enabled
                and self._last_strategy_c_shadow_status.get("active_candidate_trade")
            )
            or (
                self.config.tunables.sr_momentum_breakout_enabled
                and (
                    self._last_strategy_d_paper_status.get("execution_signal")
                    or self._last_strategy_d_paper_status.get("active_execution_trade")
                )
            )
        )
        if not (
            strategy_a_data_ready
            or features.data_ready
            or features.breakout_data_ready
            or candidate_entry_available
            or (bypass and features.spot_price > 0)
        ):
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "DATA_UNAVAILABLE", "reason": features.data_reason}

        # 4. If active positions reached limit, do not seek new entries
        if len(self._active_trades_cache) >= self.config.risk.max_concurrent_positions:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "MAX_CONCURRENT_POSITIONS_REACHED", "active_count": len(self._active_trades_cache)}

        # 5. Check if Auto Trade is enabled
        if not self.config.auto_trade_enabled or self.config.mode == AutoTradingMode.DISABLED:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "AUTO_TRADE_DISABLED"}

        # 6. Apply strategy-specific entry windows.  Strategy A must not be
        # silently gated by the shared legacy 09:20 schedule.
        strategy_a_window = self.position_manager.is_within_strategy_a_entry_window(now)
        strategy_b_window = self.position_manager.is_within_entry_window()
        enabled_window = (
            strategy_a_window if self.config.tunables.trend_pullback_enabled and not self.config.tunables.volatility_breakout_enabled
            else strategy_b_window if self.config.tunables.volatility_breakout_enabled and not self.config.tunables.trend_pullback_enabled
            else strategy_a_window or strategy_b_window
        )
        if not (enabled_window or self._active_overrides.bypass_entry_window):
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "OUTSIDE_ENTRY_WINDOW"}

        # 7. Check Cooldown after loss
        if self._last_loss_exit_time:
            mins_since_loss = (now - self._last_loss_exit_time).total_seconds() / 60.0
            if mins_since_loss < self.config.risk.cooldown_after_loss_min:
                self._reset_setups(now)
                await self._save_runtime()
                return {
                    "status": "IN_LOSS_COOLDOWN",
                    "cooldown_remaining_min": round(self.config.risk.cooldown_after_loss_min - mins_since_loss, 1),
                }

        # 8. Check Daily Trade Count Limit
        today_trades = await self.repo.list_trades(limit=1000)
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = now.astimezone(ist).date()
        today_trades = [t for t in today_trades if t.entry_time.astimezone(ist).date() == today_str]
        today_count = len(today_trades)
        loss_r = sum(t.realized_r or 0 for t in today_trades)
        pnl = sum(t.net_pnl or 0 for t in today_trades)
        if loss_r <= -self.config.risk.max_daily_loss_r or pnl <= -self.config.risk.account_equity*self.config.risk.max_daily_loss_pct/100:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "DAILY_LOSS_LIMIT_REACHED"}
        losses = [t for t in today_trades if t.exit_time and (t.net_pnl or 0) < 0]
        if losses:
            last_loss = max(t.exit_time for t in losses)
            if (now-last_loss).total_seconds() < 60*self.config.risk.cooldown_after_loss_min:
                self._reset_setups(now)
                await self._save_runtime()
                return {"status": "IN_LOSS_COOLDOWN"}
        if today_count >= self.config.risk.max_trades_per_day:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "DAILY_TRADE_LIMIT_REACHED", "today_trades": today_count}

        if self.config.mode == AutoTradingMode.LIVE and not self.config.system_armed:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "LIVE_SYSTEM_NOT_ARMED"}

        # 9. Evaluate Strategy Entry Signals only on fresh real-time input.
        # Existing positions were managed above; these gates affect new
        # exposure only.
        candles_5m, candles_15m, futures_candles = self._market_snapshot

        execution_feed_healthy = bool(
            self._market_data_status.get("execution_feed_healthy")
        )
        strategy_a_entry_data_ready = bool(
            execution_feed_healthy
            and self._market_data_status.get(
                "strategy_a_signal_data_fresh",
                False,
            )
        )
        strategy_b_entry_data_ready = bool(
            execution_feed_healthy
            and self._market_data_status.get(
                "strategy_b_signal_data_fresh",
                False,
            )
        )

        signal: Optional[StrategySignal] = None
        entry_data_blockers: dict[str, list[str]] = {}

        if self.config.tunables.trend_pullback_enabled:
            if strategy_a_entry_data_ready:
                signal = self.strategy_a.evaluate(
                    features,
                    candles_5m,
                    candles_15m,
                    futures_candles=futures_candles,
                    overrides=self._active_overrides,
                )
                await self._record_strategy_a_evaluation(features, signal)
                await self._save_runtime()
            else:
                self.strategy_a.reset(now)
                reasons = list(
                    self._market_data_status.get(
                        "execution_feed_reasons",
                        [],
                    )
                )
                if not self._market_data_status.get(
                    "strategy_a_signal_data_fresh",
                    False,
                ):
                    reasons.append("STALE_OR_MISSING_FUTURES_15M_CANDLE")
                entry_data_blockers["TREND_PULLBACK"] = list(
                    dict.fromkeys(reasons)
                )
                await self._save_runtime()

        if not signal and self.config.tunables.volatility_breakout_enabled:
            if strategy_b_entry_data_ready:
                signal = self.strategy_b.evaluate(
                    features,
                    candles_5m,
                    candles_15m,
                    overrides=self._active_overrides,
                )
                await self._save_runtime()
            else:
                self.strategy_b.reset(now)
                reasons = list(
                    self._market_data_status.get(
                        "execution_feed_reasons",
                        [],
                    )
                )
                if not self._market_data_status.get(
                    "strategy_b_signal_data_fresh",
                    False,
                ):
                    reasons.append("STALE_OR_MISSING_SPOT_5M_CANDLE")
                entry_data_blockers["VOLATILITY_BREAKOUT"] = list(
                    dict.fromkeys(reasons)
                )
                await self._save_runtime()
        elif not self.config.tunables.volatility_breakout_enabled:
            self.strategy_b.reset(now)
            await self._save_runtime()

        if not signal and self.config.tunables.di_continuation_enabled:
            signal = strategy_c_signal_from_status(
                self._last_strategy_c_shadow_status,
                as_of=now,
            )

        if not signal and self.config.tunables.sr_momentum_breakout_enabled:
            signal = strategy_d_signal_from_status(
                self._last_strategy_d_paper_status,
                as_of=now,
            )

        candidate_signal_already_persisted = False
        if signal and self._is_candidate_execution_strategy(signal.strategy):
            prior_signals = await self.repo.list_strategy_signals(limit=1000)
            prior_ids = {
                str(item.get("signal_id"))
                for item in prior_signals
                if isinstance(item, dict) and item.get("signal_id")
            }
            candidate_signal_already_persisted = signal.signal_id in prior_ids
            if candidate_signal_already_persisted and any(
                str(trade.signal_id or "") == signal.signal_id
                for trade in today_trades
            ):
                signal = None

        if not signal:
            enabled_blocked = (
                (
                    self.config.tunables.trend_pullback_enabled
                    and not strategy_a_entry_data_ready
                )
                or (
                    self.config.tunables.volatility_breakout_enabled
                    and not strategy_b_entry_data_ready
                )
            )
            return {
                "status": (
                    "ENTRY_DATA_UNHEALTHY"
                    if enabled_blocked
                    else "NO_SIGNAL"
                ),
                "entry_data_blockers": entry_data_blockers,
                "features": features.model_dump(mode="json"),
            }

        if sum(t.strategy == signal.strategy for t in today_trades) >= self.config.risk.max_trades_per_strategy_per_day:
            return {"status":"STRATEGY_DAILY_TRADE_LIMIT_REACHED"}

        failures = sum(t.strategy == signal.strategy and t.exit_time is not None and (t.net_pnl or 0) < 0 for t in today_trades)
        if failures >= self.config.risk.max_failed_trades_per_strategy:
            return {"status": "STRATEGY_FAILURE_LIMIT_REACHED"}

        # Persist candidate signals once while allowing a fresh candidate to
        # retry transient downstream failures until a trade exists or the
        # frozen signal freshness window expires.
        if not candidate_signal_already_persisted:
            await self.repo.save_strategy_signal(signal)
            await self._log_decision(
                category="SETUP",
                strategy=signal.strategy.value,
                message=f"Setup Triggered: {signal.strategy.value} {signal.direction.value} ({signal.option_type.value})",
                details={
                    "underlying_entry_price": signal.underlying_entry_price or signal.spot_reference_price,
                    "stop": signal.structural_stop,
                    "r_points": signal.r_points,
                    "derivatives_score": signal.derivatives_score,
                },
            )

        # 10. Select execution contract downstream of the underlying signal.
        execution_mode = self._execution_mode_for_signal(signal)
        chain = await self._get_option_chain()
        is_strategy_a = self._is_strategy_a(signal.strategy)
        delta_aware_selector = self._uses_delta_aware_selector(signal.strategy)
        underlying_risk_sizing = self._uses_underlying_risk_sizing(signal.strategy)
        if (
            delta_aware_selector
            and execution_mode == AutoTradingMode.LIVE
            and not bool(
                self._market_data_status.get(
                    "strategy_a_option_execution_ready",
                    False,
                )
            )
        ):
            reason = str(
                self._market_data_status.get(
                    "strategy_a_option_execution_reason"
                )
                or "VERIFIED_OPTION_DELTA_UNAVAILABLE"
            )
            await self._log_decision(
                "RISK",
                signal.strategy.value,
                "Delta-aware LIVE entry blocked: option execution readiness degraded",
                {"reason": reason, "signal_id": signal.signal_id},
            )
            await self._reject_strategy_a_execution(
                signal,
                f"EXECUTION_REJECTED_OPTION_READINESS:{reason}",
            )
            return {
                "status": (
                    "STRATEGY_A_OPTION_EXECUTION_BLOCKED"
                    if is_strategy_a
                    else "DELTA_AWARE_OPTION_EXECUTION_BLOCKED"
                ),
                "reason": reason,
            }
        if underlying_risk_sizing and signal.underlying_entry_price is None:
            await self._log_decision(
                "RISK",
                signal.strategy.value,
                "Structural-risk strategy signal missing authoritative underlying entry",
                signal.model_dump(mode="json"),
            )
            if is_strategy_a:
                await self._reject_strategy_a_execution(
                    signal,
                    "EXECUTION_REJECTED_INVALID_ENTRY_REFERENCE",
                )
            return {"status": "INVALID_UNDERLYING_ENTRY_REFERENCE"}
        underlying_entry = (
            signal.underlying_entry_price
            if underlying_risk_sizing
            else signal.spot_reference_price
        )
        selector_underlying = underlying_entry
        selected_contract, candidates, rejection_reason = self.contract_selector.select_contract(
            direction=signal.direction,
            spot_price=selector_underlying,
            option_chain=chain,
            override_premium_cap=self._active_overrides.max_option_premium_cap,
            strategy_a=delta_aware_selector,
            as_of=signal.timestamp,
        )
        if (
            signal.strategy in {
                StrategyName.TREND_PULLBACK,
                StrategyName.DI_CONTINUATION,
                StrategyName.SR_MOMENTUM_BREAKOUT,
            }
            and chain.get("source") not in ("BREEZE", "KITE", "LIVE")
        ):
            selected_contract = None
            rejection_reason = chain.get("validation_rejection", "NO_REAL_OPTION_QUOTE")
        # Passive shadow capture only. The selector has already run and its
        # result is never changed by this recorder; persistence failures are
        # intentionally non-blocking for paper/live execution paths.
        if signal.strategy in {
            StrategyName.TREND_PULLBACK,
            StrategyName.VOLATILITY_BREAKOUT,
            StrategyName.DI_CONTINUATION,
            StrategyName.SR_MOMENTUM_BREAKOUT,
        }:
            try:
                await self._capture_option_chain_snapshot(
                    signal=signal,
                    spot_price=selector_underlying,
                    chain=chain,
                    selector_candidates=candidates,
                    selected_contract=selected_contract,
                    rejection_reason=rejection_reason,
                    execution_mode=execution_mode,
                )
            except Exception:
                logger.exception("Passive option-chain snapshot capture failed")

        if not selected_contract:
            if is_strategy_a:
                await self._persist_strategy_a_telemetry(StrategyAEvaluationRecord(
                    timestamp=features.timestamp.isoformat(),
                    futures_contract=signal.features_snapshot.get("futures_contract", "UNAVAILABLE"),
                    completed_candle_timestamp=signal.features_snapshot.get("completed_candle_timestamp", features.timestamp.isoformat()),
                    trigger=signal.features_snapshot.get("trigger"), structural_stop=signal.structural_stop,
                    underlying_r=signal.r_points, strategy_state=self.strategy_a.snapshot.state.value,
                    rejection_or_invalidation_reason=rejection_reason,
                    management_event="CONTRACT_SELECTION_REJECTED",
                ))
            await self._log_decision(
                category="CONTRACT_SELECTION",
                strategy=signal.strategy.value,
                message=f"Contract selection failed: {rejection_reason}",
                details={"candidates_checked": len(candidates), "cap": self.config.option_selection.max_option_premium},
            )
            if is_strategy_a:
                await self._reject_strategy_a_execution(signal, "EXECUTION_REJECTED_CONTRACT_SELECTION")
            return {"status": "CONTRACT_SELECTION_FAILED", "reason": rejection_reason}

        await self._log_decision(
            category="CONTRACT_SELECTION",
            strategy=signal.strategy.value,
            message=f"Selected {selected_contract.symbol} @ ₹{selected_contract.ask_price} (Cap: ₹{self.config.option_selection.max_option_premium})",
            details=selected_contract.model_dump(mode="json"),
        )
        if is_strategy_a:
            await self._persist_strategy_a_telemetry(StrategyAEvaluationRecord(
                timestamp=features.timestamp.isoformat(),
                futures_contract=signal.features_snapshot.get("futures_contract", "UNAVAILABLE"),
                completed_candle_timestamp=signal.features_snapshot.get("completed_candle_timestamp", features.timestamp.isoformat()),
                trigger=signal.features_snapshot.get("trigger"), structural_stop=signal.structural_stop,
                underlying_r=signal.r_points, strategy_state=self.strategy_a.snapshot.state.value,
                option_contract=selected_contract.instrument_id, expiry=selected_contract.expiry,
                delta=selected_contract.delta, gamma=selected_contract.gamma,
                delta_source=selected_contract.greek_source,
                quote_timestamp=selected_contract.quote_timestamp.isoformat() if selected_contract.quote_timestamp else None,
                quote_freshness_seconds=selected_contract.quote_freshness_seconds,
                bid=selected_contract.bid_price, ask=selected_contract.ask_price,
                spread=selected_contract.spread_pct, management_event="CONTRACT_SELECTED",
            ))

        # 11. Position sizing. Strategy C, like A, has an explicit
        # underlying structural stop and therefore uses delta-aware sizing.
        sizing = None
        if underlying_risk_sizing:
            sizing = self.risk_sizer.size(
                underlying_entry=underlying_entry,
                underlying_stop=signal.structural_stop,
                option_delta=selected_contract.delta,
                lot_size=selected_contract.lot_size,
                option_entry=selected_contract.ask_price,
                account_equity=self.config.risk.account_equity,
            )
            lots, quantity = sizing.lots, sizing.quantity
        else:
            lots, quantity = self.position_manager.calculate_position_size(
                entry_premium=selected_contract.ask_price,
                account_equity=self.config.risk.account_equity,
                lot_size=selected_contract.lot_size,
            )

        if lots < 1:
            if is_strategy_a:
                await self._persist_strategy_a_telemetry(StrategyAEvaluationRecord(
                    timestamp=features.timestamp.isoformat(),
                    futures_contract=signal.features_snapshot.get("futures_contract", "UNAVAILABLE"),
                    completed_candle_timestamp=signal.features_snapshot.get("completed_candle_timestamp", features.timestamp.isoformat()),
                    trigger=signal.features_snapshot.get("trigger"), structural_stop=signal.structural_stop,
                    underlying_r=signal.r_points, strategy_state=self.strategy_a.snapshot.state.value,
                    option_contract=selected_contract.instrument_id, expiry=selected_contract.expiry,
                    delta=selected_contract.delta, delta_source=selected_contract.greek_source,
                    position_size=0, lot_size=selected_contract.lot_size, lots=0,
                    risk_budget=sizing.risk_budget if sizing is not None else None,
                    estimated_option_loss_at_structural_stop=sizing.option_loss_per_lot if sizing is not None else None,
                    rejection_or_invalidation_reason=(sizing.rejection_reason if sizing is not None else None) or "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET",
                    management_event="SIZING_REJECTED",
                ))
            await self._log_decision(
                category="RISK",
                strategy=signal.strategy.value,
                message="Position sizing rejected: calculated lots < 1",
                details={"capital_cap": self.config.risk.max_trade_capital, "premium": selected_contract.ask_price},
            )
            if is_strategy_a:
                await self._reject_strategy_a_execution(signal, "EXECUTION_REJECTED_SIZING")
            return {"status": "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"}

        # 12. Check LIVE Arming Gate for non-Strategy-A compatibility paths.
        if execution_mode == AutoTradingMode.LIVE and not self._live_orders_enabled():
            return {"status": "LIVE_TRADING_DISABLED"}
        if execution_mode == AutoTradingMode.LIVE and not self.config.system_armed:
            await self._log_decision(
                category="SECURITY",
                strategy=signal.strategy.value,
                message="LIVE order execution blocked: System is not ARMED",
                details={"symbol": selected_contract.symbol, "lots": lots, "quantity": quantity},
            )
            return {"status": "LIVE_SYSTEM_NOT_ARMED"}

        # 13. Create Active Trade and dispatch Order
        trade_id = f"TRD-{int(now.timestamp())}"
        entry_slippage = self._paper_slippage() if execution_mode in (AutoTradingMode.PAPER, AutoTradingMode.SHADOW_ONLY) else 0.0
        entry_price = round(selected_contract.ask_price + entry_slippage, 2)
        hard_stop_price = round(
            entry_price * (1.0 - (self.config.risk.option_hard_stop_pct / 100.0)),
            2,
        )

        new_trade = ActiveTrade(
            trade_id=trade_id,
            mode=execution_mode,
            strategy=signal.strategy,
            direction=signal.direction,
            option_type=signal.option_type,
            contract_symbol=selected_contract.symbol,
            contract_instrument_id=selected_contract.instrument_id,
            expiry=selected_contract.expiry,
            strike=selected_contract.strike,
            quantity=quantity,
            lot_size=selected_contract.lot_size,
            lots=lots,
            entry_time=now,
            entry_option_price=entry_price,
            entry_spot_price=underlying_entry,
            initial_structural_stop=signal.structural_stop,
            initial_r_points=signal.r_points,
            pullback_swing_low=signal.features_snapshot.get("pullback_low"),
            pullback_swing_high=signal.features_snapshot.get("pullback_high"),
            box_high=signal.features_snapshot.get("box_high"),
            box_low=signal.features_snapshot.get("box_low"),
            atr_at_lock=signal.features_snapshot.get("atr_at_lock"),
            current_option_price=selected_contract.ltp or selected_contract.ask_price,
            current_spot_price=underlying_entry,
            current_trailing_stop=signal.structural_stop,
            option_hard_stop_price=hard_stop_price,
            current_r=0.0,
            peak_r=0.0,
            state=TradeLifecycleState.ENTRY_PENDING if execution_mode == AutoTradingMode.LIVE else TradeLifecycleState.OPEN_INITIAL_RISK,
            signal_id=signal.signal_id,
            selector_timestamp=now,
            selected_contract_snapshot=selected_contract.model_dump(mode="json"),
            entry_bid=selected_contract.bid_price,
            entry_ask=selected_contract.ask_price,
            entry_ltp=selected_contract.ltp,
            entry_quote_source=chain.get("source"),
            entry_quote_timestamp=now,
            entry_quote_freshness_seconds=0.0,
            entry_slippage_points=entry_slippage,
            entry_raw_ask=selected_contract.ask_price,
            entry_executable_price=entry_price,
            current_bid=selected_contract.bid_price,
            current_ask=selected_contract.ask_price,
            current_ltp=selected_contract.ltp,
            current_quote_source=chain.get("source"),
            current_quote_timestamp=now,
            current_quote_freshness_seconds=0.0,
            current_quote_volume=selected_contract.volume,
            current_quote_open_interest=selected_contract.open_interest,
            option_data_status="ENTRY_CAPTURED",
            cost_assumption_version=self.config.risk.paper_cost_assumption_version,
            cost_assumptions=self._cost_metadata(),
            futures_contract_id=signal.features_snapshot.get("futures_contract"),
            underlying_entry_price=underlying_entry,
            underlying_current_price=underlying_entry,
            underlying_structural_stop=signal.structural_stop,
            underlying_r=signal.r_points,
            initial_quantity=quantity,
            remaining_quantity=quantity,
            selected_option_delta=selected_contract.delta,
            selected_option_delta_source=selected_contract.greek_source,
            selected_option_gamma=selected_contract.gamma,
            selected_option_gamma_source=selected_contract.greek_source,
            risk_budget=sizing.risk_budget if sizing is not None else None,
            estimated_option_loss_at_structural_stop=(
                sizing.option_loss_per_lot * lots
                if sizing is not None
                else None
            ),
        )

        await self.repo.save_trade(new_trade)
        if is_strategy_a:
            await self._record_strategy_a_lifecycle_event(
                new_trade, features, "POSITION_SIZED"
            )
            # PAPER/SHADOW entries are immediate simulations.  A LIVE Strategy A
            # trade is not ENTERED until the broker fill is confirmed below.
            if execution_mode != AutoTradingMode.LIVE:
                self.strategy_a.confirm_entry(signal.timestamp)
                await self._save_runtime()

        if execution_mode != AutoTradingMode.LIVE:
            await self._record_execution({
                "trade_id": new_trade.trade_id,
                "side": "BUY",
                "timestamp": now.isoformat(),
                "raw_bid": selected_contract.bid_price,
                "raw_ask": selected_contract.ask_price,
                "raw_ltp": selected_contract.ltp,
                "executable_price": entry_price,
                "slippage_points": entry_slippage,
                "quantity": quantity,
                "source": chain.get("source", "UNKNOWN"),
                "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                "reason": "PAPER_OR_SHADOW_ENTRY",
            })
        if is_strategy_a and execution_mode != AutoTradingMode.LIVE:
            await self._record_strategy_a_lifecycle_event(
                new_trade, features, "ENTRY_OPENED"
            )
        self._active_trades_cache.append(new_trade)

        await self._log_decision(
            category="ORDER",
            strategy=signal.strategy.value,
            message=f"ORDER ENTERED: {new_trade.mode.value} {new_trade.direction.value} {new_trade.contract_symbol} x {quantity} @ ₹{selected_contract.ask_price}",
            details=new_trade.model_dump(mode="json"),
        )

        # In LIVE mode, dispatch OrderIntent to OMS
        if execution_mode == AutoTradingMode.LIVE:
            intent = OrderIntent(
                intent_id=generate_id(),
                correlation_id=trade_id,
                strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
                source=SourceType.STRATEGY,
                instrument_id=selected_contract.instrument_id,
                symbol=selected_contract.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=selected_contract.ask_price,
                product=ProductType.OPTIONS,
                trading_mode=TradingMode.LIVE,
            )
            order = await self.oms.create_order_intent(intent)
            new_trade.entry_order_id = order.order_id
            new_trade.state = TradeLifecycleState.ENTRY_PENDING
            await self.repo.save_trade(new_trade)

        # Broadcast update
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.STRATEGY_SIGNAL,
                payload={"event": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")},
            )
        )

        return {"status": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")}

    @staticmethod
    def _option_tick_price(value: float, *, down: bool = False) -> float:
        """Normalize an option price to the platform's NFO tick."""
        tick = 0.05
        if value <= 0:
            return tick
        steps = int(value / tick) if down else round(value / tick)
        return round(max(tick, steps * tick), 2)

    def _protective_stop_prices(self, trade: ActiveTrade) -> tuple[float, float]:
        trigger = self._option_tick_price(float(trade.option_hard_stop_price))
        raw_limit = trigger * (
            1.0 - self.config.risk.broker_protective_stop_limit_buffer_pct / 100.0
        )
        limit_price = self._option_tick_price(raw_limit, down=True)
        if limit_price >= trigger:
            limit_price = self._option_tick_price(trigger - 0.05, down=True)
        return trigger, limit_price

    async def _record_protective_stop_failure(
        self,
        trade: ActiveTrade,
        reason: str,
        *,
        state_unknown: bool = False,
    ) -> bool:
        """Persist a protection failure and escalate when safe recovery ends."""
        trade.protective_stop_failures += 1
        trade.protective_stop_last_failure_reason = reason
        trade.protective_stop_last_failure_at = utc_now()
        trade.option_data_status = "PROTECTIVE_STOP_DEGRADED"

        exhausted = (
            state_unknown
            or trade.protective_stop_failures
            >= self.config.risk.broker_protective_stop_max_failures
        )
        if exhausted:
            trade.protective_stop_status = (
                "STATE_UNKNOWN_BLOCKED"
                if state_unknown
                else "UNAVAILABLE_EXIT_REQUIRED"
            )
            if not state_unknown:
                trade.pending_exit_reason = (
                    BROKER_PROTECTIVE_STOP_UNAVAILABLE
                )
                trade.option_exit_reason = (
                    BROKER_PROTECTIVE_STOP_UNAVAILABLE
                )
            if self.config.system_armed:
                self.config.system_armed = False
                await self.repo.save_auto_config(self.config)
            await self._log_decision(
                "SECURITY",
                trade.strategy.value,
                "Broker protective stop safety escalation",
                {
                    "trade_id": trade.trade_id,
                    "reason": reason,
                    "state_unknown": state_unknown,
                    "failure_count": trade.protective_stop_failures,
                    "system_auto_disarmed": True,
                },
            )
        await self.repo.save_trade(trade)
        return exhausted

    async def _sync_live_protective_stop(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
    ) -> bool:
        """Keep the LIVE long option protected without racing duplicate SELLs."""
        if trade.mode != AutoTradingMode.LIVE or trade.filled_quantity <= 0:
            return True
        if trade.state == TradeLifecycleState.CLOSED:
            return False

        remaining_quantity = max(
            0,
            trade.quantity - trade.exit_filled_quantity,
        )
        if remaining_quantity <= 0:
            if trade.exit_proceeds > 0 and trade.quantity > 0:
                await self._close_trade(
                    trade,
                    features,
                    trade.exit_proceeds / trade.quantity,
                    OPTION_EMERGENCY_STOP,
                    quote={"source": "BROKER_PROTECTIVE_STOP"},
                )
            return False

        if trade.protective_stop_order_id:
            protective = await self.oms.get_order(
                trade.protective_stop_order_id
            )
            if protective is None:
                await self._record_protective_stop_failure(
                    trade,
                    "OMS_PROTECTIVE_ORDER_MISSING",
                    state_unknown=True,
                )
                return False

            status = protective.status.value
            trade.protective_stop_status = status
            new_filled = int(protective.filled_quantity or 0)
            accounted = int(
                trade.protective_stop_filled_quantity or 0
            )
            if new_filled >= accounted:
                cumulative_average = float(
                    protective.average_price
                    or trade.protective_stop_limit_price
                    or trade.option_hard_stop_price
                )
                new_proceeds = cumulative_average * new_filled
                delta_filled = new_filled - accounted
                delta_proceeds = (
                    new_proceeds
                    - trade.protective_stop_filled_proceeds
                )
                if delta_filled or abs(delta_proceeds) > 1e-9:
                    trade.exit_filled_quantity = min(
                        trade.quantity,
                        trade.exit_filled_quantity + delta_filled,
                    )
                    trade.exit_proceeds += delta_proceeds
                    trade.protective_stop_filled_quantity = new_filled
                    trade.protective_stop_filled_proceeds = new_proceeds
                    if delta_filled > 0:
                        incremental_price = (
                            delta_proceeds / delta_filled
                            if delta_proceeds > 0
                            else cumulative_average
                        )
                        await self._record_execution({
                            "ledger_id": (
                                f"LIVE-PROTECTIVE-"
                                f"{getattr(protective, 'order_id', None) or trade.protective_stop_order_id or protective.broker_order_id}-"
                                f"{new_filled}"
                            ),
                            "trade_id": trade.trade_id,
                            "side": "SELL",
                            "timestamp": (
                                getattr(protective, "updated_at", None)
                                or utc_now()
                            ).isoformat(),
                            "raw_bid": None,
                            "raw_ask": None,
                            "raw_ltp": None,
                            "executable_price": incremental_price,
                            "slippage_points": 0.0,
                            "quantity": delta_filled,
                            "source": "BROKER_PROTECTIVE_STOP",
                            "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                            "reason": OPTION_EMERGENCY_STOP,
                        })

            # Broker reconciliation above may have discovered a partial fill.
            # Any replacement protection must cover only the still-open long
            # quantity, never the pre-reconciliation quantity.
            remaining_quantity = max(
                0,
                trade.quantity - trade.exit_filled_quantity,
            )

            if (
                status == "FILLED"
                or trade.exit_filled_quantity >= trade.quantity
            ):
                trade.protective_stop_status = "FILLED"
                average_exit = (
                    trade.exit_proceeds / trade.quantity
                    if trade.quantity > 0
                    else float(protective.average_price or 0.0)
                )
                await self._close_trade(
                    trade,
                    features,
                    average_exit,
                    OPTION_EMERGENCY_STOP,
                    quote={
                        "bid": float(
                            protective.average_price or average_exit
                        ),
                        "source": "BROKER_PROTECTIVE_STOP",
                    },
                )
                return False

            terminal_failure = status in {
                "CANCELLED",
                "REJECTED",
                "RISK_REJECTED",
                "EXPIRED",
                "FAILED_SAFE",
            }
            if terminal_failure:
                cancelled_for_exit = (
                    trade.protective_stop_cancel_for_exit
                    and status == "CANCELLED"
                )
                trade.protective_stop_order_id = None
                trade.protective_stop_filled_quantity = 0
                trade.protective_stop_filled_proceeds = 0.0
                trade.protective_stop_cancel_for_exit = False
                trade.protective_stop_cancel_attempts = 0
                trade.protective_stop_cancel_requested_at = None

                if cancelled_for_exit:
                    await self.repo.save_trade(trade)
                    return True

                exhausted = await self._record_protective_stop_failure(
                    trade,
                    f"PROTECTIVE_STOP_{status}",
                )
                if exhausted:
                    # The old protective order is confirmed terminal, so it is
                    # safe to proceed to a reduce-only flattening exit.
                    return True
                # Retry protection immediately below without waiting for a
                # future scheduler cycle.
            else:
                if status in {
                    "OPEN",
                    "ACKNOWLEDGED",
                    "PARTIALLY_FILLED",
                }:
                    trade.protective_stop_failures = 0
                    trade.protective_stop_last_failure_reason = None
                    trade.protective_stop_last_failure_at = None
                await self.repo.save_trade(trade)
                if trade.protective_stop_cancel_for_exit:
                    return False
                return status in {
                    "OPEN",
                    "ACKNOWLEDGED",
                    "PARTIALLY_FILLED",
                }

        if (
            trade.pending_exit_reason
            or trade.exit_order_id
            or trade.partial_exit_order_id
        ):
            return True

        trigger_price, limit_price = self._protective_stop_prices(trade)
        intent = OrderIntent(
            intent_id=generate_id(),
            correlation_id=trade.trade_id,
            strategy_instance_id=(
                "INST-NIFTY-AUTO-ENGINE-PROTECTIVE"
            ),
            source=SourceType.STRATEGY,
            instrument_id=trade.contract_instrument_id,
            symbol=trade.contract_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT,
            quantity=remaining_quantity,
            price=limit_price,
            trigger_price=trigger_price,
            product=ProductType.OPTIONS,
            trading_mode=TradingMode.LIVE,
            reduce_only=True,
        )
        order = await self.oms.create_order_intent(intent)
        trade.protective_stop_order_id = order.order_id
        trade.protective_stop_filled_quantity = 0
        trade.protective_stop_filled_proceeds = 0.0
        trade.protective_stop_status = order.status.value
        trade.protective_stop_trigger_price = trigger_price
        trade.protective_stop_limit_price = limit_price
        trade.protective_stop_cancel_for_exit = False
        trade.protective_stop_cancel_attempts = 0
        trade.protective_stop_cancel_requested_at = None
        await self.repo.save_trade(trade)
        await self._log_decision(
            "RISK",
            trade.strategy.value,
            "Broker-held option catastrophe stop submitted",
            {
                "trade_id": trade.trade_id,
                "protective_order_id": order.order_id,
                "quantity": remaining_quantity,
                "trigger_price": trigger_price,
                "limit_price": limit_price,
                "prior_failures": trade.protective_stop_failures,
            },
        )
        return False

    async def _cancel_live_protective_stop_for_exit(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
    ) -> bool:
        """Cancel/reconcile protection before any competing reduce-only SELL."""
        if (
            trade.mode != AutoTradingMode.LIVE
            or not trade.protective_stop_order_id
        ):
            return True

        may_continue = await self._sync_live_protective_stop(
            trade,
            features,
        )
        if trade.state == TradeLifecycleState.CLOSED:
            return False
        if not trade.protective_stop_order_id:
            return may_continue

        if trade.protective_stop_cancel_for_exit:
            requested_at = trade.protective_stop_cancel_requested_at
            age_seconds = (
                max(
                    0.0,
                    (utc_now() - requested_at).total_seconds(),
                )
                if requested_at is not None
                else float("inf")
            )
            if (
                age_seconds
                < self.config.risk
                .broker_protective_stop_cancel_timeout_sec
            ):
                return False
            if (
                trade.protective_stop_cancel_attempts
                >= self.config.risk
                .broker_protective_stop_cancel_max_attempts
            ):
                await self._record_protective_stop_failure(
                    trade,
                    "PROTECTIVE_STOP_CANCEL_UNRESOLVED",
                    state_unknown=True,
                )
                return False
            trade.protective_stop_cancel_for_exit = False

        protective = await self.oms.get_order(
            trade.protective_stop_order_id
        )
        if protective is None:
            await self._record_protective_stop_failure(
                trade,
                "OMS_PROTECTIVE_ORDER_MISSING_DURING_CANCEL",
                state_unknown=True,
            )
            return False
        if protective.status.value == "FILLED":
            return False
        if not protective.broker_order_id:
            trade.protective_stop_status = (
                "CANCEL_WAITING_FOR_BROKER_REFERENCE"
            )
            await self.repo.save_trade(trade)
            return False

        gateway = getattr(self.hist_svc, "broker_gateway", None)
        if gateway is None:
            await self._record_protective_stop_failure(
                trade,
                "PROTECTIVE_STOP_CANCEL_NO_GATEWAY",
                state_unknown=True,
            )
            return False

        trade.protective_stop_cancel_attempts += 1
        trade.protective_stop_cancel_requested_at = utc_now()
        trade.protective_stop_cancel_for_exit = True
        try:
            response = await gateway.cancel_order(
                protective.broker_order_id,
                mode=protective.trading_mode,
            )
            if response.success:
                trade.protective_stop_status = "CANCEL_REQUESTED"
            else:
                trade.protective_stop_status = (
                    f"CANCEL_FAILED:{response.status}"
                )
                trade.protective_stop_last_failure_reason = (
                    trade.protective_stop_status
                )
                trade.protective_stop_last_failure_at = utc_now()
        except Exception as exc:
            trade.protective_stop_status = (
                f"CANCEL_ERROR:{type(exc).__name__}"
            )
            trade.protective_stop_last_failure_reason = (
                trade.protective_stop_status
            )
            trade.protective_stop_last_failure_at = utc_now()

        if (
            trade.protective_stop_cancel_attempts
            >= self.config.risk
            .broker_protective_stop_cancel_max_attempts
            and trade.protective_stop_status != "CANCEL_REQUESTED"
        ):
            await self._record_protective_stop_failure(
                trade,
                "PROTECTIVE_STOP_CANCEL_RETRIES_EXHAUSTED",
                state_unknown=True,
            )
            return False

        await self.repo.save_trade(trade)
        return False

    @staticmethod
    def _apply_exit_order_progress(
        trade: ActiveTrade,
        order: Any,
    ) -> tuple[int, float, int]:
        """Apply cumulative broker fill progress exactly once per exit order."""
        new_filled = max(
            0,
            min(int(order.filled_quantity or 0), int(order.quantity or 0)),
        )
        accounted_filled = int(
            trade.exit_order_accounted_filled_quantity or 0
        )
        if new_filled < accounted_filled:
            return 0, 0.0, new_filled

        cumulative_average = float(order.average_price or 0.0)
        new_order_proceeds = cumulative_average * new_filled
        accounted_proceeds = float(
            trade.exit_order_accounted_proceeds or 0.0
        )
        delta_filled = new_filled - accounted_filled
        delta_proceeds = new_order_proceeds - accounted_proceeds
        incremental_price = 0.0
        if delta_filled or abs(delta_proceeds) > 1e-9:
            trade.exit_filled_quantity = min(
                trade.quantity,
                trade.exit_filled_quantity + delta_filled,
            )
            trade.exit_proceeds += delta_proceeds
            trade.exit_order_accounted_filled_quantity = new_filled
            trade.exit_order_accounted_proceeds = new_order_proceeds
            if delta_filled > 0:
                incremental_price = (
                    delta_proceeds / delta_filled
                    if delta_proceeds > 0
                    else cumulative_average
                )
        return delta_filled, incremental_price, new_filled

    @staticmethod
    def _reset_exit_order_progress(trade: ActiveTrade) -> None:
        trade.exit_order_accounted_filled_quantity = 0
        trade.exit_order_accounted_proceeds = 0.0

    @staticmethod
    def _apply_live_t1_order_progress(
        trade: ActiveTrade,
        order: Any,
    ) -> tuple[int, float, int]:
        """Apply cumulative broker fills for a lifecycle T1 order once."""
        new_filled = max(
            0,
            min(int(order.filled_quantity or 0), int(order.quantity or 0)),
        )
        accounted = int(
            trade.partial_exit_order_accounted_filled_quantity or 0
        )
        if new_filled < accounted:
            return 0, 0.0, new_filled

        cumulative_average = float(order.average_price or 0.0)
        new_order_proceeds = cumulative_average * new_filled
        accounted_proceeds = float(
            trade.partial_exit_order_accounted_proceeds or 0.0
        )
        delta_filled = new_filled - accounted
        delta_proceeds = new_order_proceeds - accounted_proceeds

        if delta_filled or abs(delta_proceeds) > 1e-9:
            original_quantity = int(
                trade.initial_quantity
                or (trade.quantity + trade.partial_exit_filled_quantity)
            )
            trade.partial_exit_filled_quantity = min(
                int(trade.t1_exit_quantity),
                int(trade.partial_exit_filled_quantity) + delta_filled,
            )
            trade.partial_exit_proceeds += delta_proceeds
            if (
                trade.partial_exit_filled_quantity > 0
                and trade.partial_exit_proceeds > 0
            ):
                trade.partial_exit_price = (
                    trade.partial_exit_proceeds
                    / trade.partial_exit_filled_quantity
                )
            trade.partial_exit_order_accounted_filled_quantity = new_filled
            trade.partial_exit_order_accounted_proceeds = new_order_proceeds
            trade.partial_exit_time = (
                getattr(order, "updated_at", None) or utc_now()
            )
            trade.partial_exit_reason = "T1_REACHED_PARTIAL_EXIT"
            trade.remaining_quantity = max(
                0,
                original_quantity - trade.partial_exit_filled_quantity,
            )
            trade.quantity = trade.remaining_quantity
            trade.lots = (
                trade.remaining_quantity // trade.lot_size
                if trade.lot_size > 0
                else 0
            )
            trade.t1_exit_pending = (
                trade.partial_exit_filled_quantity
                < trade.t1_exit_quantity
            )
            if trade.partial_exit_filled_quantity > 0:
                trade.t1_realized_r = trade.t1_decision_r
        incremental_price = (
            delta_proceeds / delta_filled
            if delta_filled > 0 and delta_proceeds > 0
            else cumulative_average if delta_filled > 0 else 0.0
        )
        return delta_filled, incremental_price, new_filled

    @staticmethod
    def _reset_live_t1_order_progress(trade: ActiveTrade) -> None:
        trade.partial_exit_order_accounted_filled_quantity = 0
        trade.partial_exit_order_accounted_proceeds = 0.0

    async def _sync_live_t1_partial_exit(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
    ) -> bool:
        """Reconcile a LIVE T1 reduce-only order before further management."""
        if not trade.partial_exit_order_id:
            return True

        order = await self.oms.get_order(trade.partial_exit_order_id)
        if order is None:
            trade.option_data_status = "LIVE_T1_ORDER_STATE_UNKNOWN"
            await self.repo.save_trade(trade)
            return False

        was_complete = (
            trade.partial_exit_filled_quantity >= trade.t1_exit_quantity
        )
        (
            delta_filled,
            incremental_price,
            cumulative_filled,
        ) = self._apply_live_t1_order_progress(trade, order)
        if delta_filled > 0:
            await self._record_execution({
                "ledger_id": (
                    f"LIVE-T1-{order.order_id}-{cumulative_filled}"
                ),
                "trade_id": trade.trade_id,
                "side": "SELL",
                "timestamp": (
                    getattr(order, "updated_at", None) or utc_now()
                ).isoformat(),
                "raw_bid": trade.partial_exit_raw_bid,
                "raw_ask": None,
                "raw_ltp": None,
                "executable_price": (
                    incremental_price
                    or float(order.average_price or order.price or 0.0)
                ),
                "slippage_points": 0.0,
                "quantity": delta_filled,
                "source": "BROKER_FILL",
                "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                "reason": "T1_REACHED_PARTIAL_EXIT",
            })
        terminal = order.status.value in {
            "FILLED",
            "CANCELLED",
            "REJECTED",
            "RISK_REJECTED",
            "EXPIRED",
            "FAILED_SAFE",
        }

        if not terminal:
            await self.repo.save_trade(trade)
            return False

        trade.partial_exit_order_id = None
        self._reset_live_t1_order_progress(trade)
        target_complete = (
            trade.partial_exit_filled_quantity >= trade.t1_exit_quantity
        )
        trade.t1_exit_pending = not target_complete
        if target_complete:
            trade.option_data_status = "LIVE_T1_EXIT_FILLED"
            if not was_complete and self._is_strategy_a(trade.strategy):
                await self._record_strategy_a_lifecycle_event(
                    trade,
                    features,
                    "PARTIAL_EXIT",
                    "T1_REACHED_PARTIAL_EXIT",
                )
        elif delta_filled > 0:
            trade.option_data_status = "LIVE_T1_EXIT_PARTIAL"
        else:
            trade.option_data_status = (
                f"LIVE_T1_EXIT_{order.status.value}"
            )
        await self.repo.save_trade(trade)

        # The T1 order no longer competes with the catastrophe stop. Restore
        # protection for the actual remaining long quantity before proceeding.
        if trade.quantity > 0:
            return await self._sync_live_protective_stop(trade, features)
        return True

    async def _submit_live_t1_partial_exit(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
        quote: dict[str, Any],
    ) -> None:
        """Submit the outstanding T1 quantity through OMS."""
        if trade.partial_exit_order_id:
            return

        outstanding = max(
            0,
            int(trade.t1_exit_quantity)
            - int(trade.partial_exit_filled_quantity),
        )
        quantity = min(outstanding, max(0, int(trade.quantity)))
        if quantity <= 0:
            trade.t1_exit_pending = False
            await self.repo.save_trade(trade)
            return

        if not await self._cancel_live_protective_stop_for_exit(
            trade,
            features,
        ):
            trade.t1_exit_pending = True
            await self.repo.save_trade(trade)
            return

        raw_bid = float(quote.get("bid") or 0.0)
        if raw_bid <= 0:
            trade.t1_exit_pending = True
            trade.option_data_status = "LIVE_T1_EXIT_WAITING_FOR_BID"
            await self.repo.save_trade(trade)
            return

        order_price = self._option_tick_price(raw_bid, down=True)
        intent = OrderIntent(
            intent_id=generate_id(),
            correlation_id=trade.trade_id,
            strategy_instance_id="INST-NIFTY-AUTO-ENGINE-T1",
            source=SourceType.STRATEGY,
            instrument_id=trade.contract_instrument_id,
            symbol=trade.contract_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=order_price,
            product=ProductType.OPTIONS,
            time_in_force=TimeInForce.IOC,
            trading_mode=TradingMode.LIVE,
            reduce_only=True,
        )
        order = await self.oms.create_order_intent(intent)
        trade.partial_exit_order_id = order.order_id
        self._reset_live_t1_order_progress(trade)
        trade.partial_exit_raw_bid = raw_bid
        trade.t1_exit_pending = True
        trade.option_data_status = "LIVE_T1_EXIT_SUBMITTED"
        await self.repo.save_trade(trade)

    async def _submit_live_final_exit(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
        quote: dict[str, Any],
        reason: str,
    ) -> None:
        """Route a lifecycle final LIVE exit through one reduce-only OMS order."""
        if trade.exit_order_id:
            return

        if is_option_emergency_stop(reason):
            # Catastrophe protection already owns this exit. Never race a
            # second SELL against the broker-held stop.
            trade.pending_exit_reason = OPTION_EMERGENCY_STOP
            trade.option_exit_reason = OPTION_EMERGENCY_STOP
            await self.repo.save_trade(trade)
            return

        if not await self._cancel_live_protective_stop_for_exit(
            trade,
            features,
        ):
            trade.pending_exit_reason = reason
            await self.repo.save_trade(trade)
            return

        bid = float(quote.get("bid") or 0.0)
        if bid <= 0:
            trade.pending_exit_reason = reason
            trade.option_data_status = "LIVE_EXIT_WAITING_FOR_BID"
            await self.repo.save_trade(trade)
            return

        quantity = max(
            0,
            int(trade.quantity) - int(trade.exit_filled_quantity),
        )
        if quantity <= 0:
            await self.repo.save_trade(trade)
            return

        intent = OrderIntent(
            intent_id=generate_id(),
            correlation_id=trade.trade_id,
            strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
            source=SourceType.STRATEGY,
            instrument_id=trade.contract_instrument_id,
            symbol=trade.contract_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=self._option_tick_price(bid, down=True),
            product=ProductType.OPTIONS,
            trading_mode=TradingMode.LIVE,
            reduce_only=True,
        )
        order = await self.oms.create_order_intent(intent)
        trade.exit_order_id = order.order_id
        self._reset_exit_order_progress(trade)
        trade.pending_exit_reason = reason
        trade.state = TradeLifecycleState.EXIT_PENDING
        trade.option_data_status = "LIVE_EXIT_SUBMITTED"
        await self.repo.save_trade(trade)

    def _candidate_runtime_for_trade(
        self,
        trade: ActiveTrade,
    ) -> dict[str, Any] | None:
        """Return the frozen C/D underlying lifecycle for an integrated trade."""
        if trade.strategy == StrategyName.DI_CONTINUATION:
            status = self._last_strategy_c_shadow_status
        elif trade.strategy == StrategyName.SR_MOMENTUM_BREAKOUT:
            status = self._last_strategy_d_paper_status
        else:
            return None

        candidates: list[dict[str, Any]] = []
        active_execution = status.get("active_execution_trade")
        if isinstance(active_execution, dict):
            candidates.append(active_execution)
        active_paper = status.get("active_paper_trade")
        if isinstance(active_paper, dict):
            candidates.append(active_paper)
        candidates.extend(
            row
            for row in (status.get("paper_trades") or [])
            if isinstance(row, dict)
        )
        for row in candidates:
            if str(row.get("signal_id") or "") == str(trade.signal_id or ""):
                return row

        # C's underlying lifecycle exists independently of its parallel paper
        # option observer. Keep LIVE management tied to that frozen lifecycle.
        if trade.strategy == StrategyName.DI_CONTINUATION:
            row = status.get("active_candidate_trade") or {}
            if str(row.get("candidate_signal_id") or "") == str(
                trade.signal_id or ""
            ):
                lifecycle = row.get("lifecycle") or {}
                return {
                    "signal_id": trade.signal_id,
                    "current_underlying_stop": lifecycle.get("current_stop"),
                    "current_r": lifecycle.get("current_r"),
                    "underlying_exit_reason": lifecycle.get("exit_reason"),
                    "underlying_exit_time": lifecycle.get("exit_time"),
                    "underlying_exit_price": lifecycle.get("exit_price"),
                    "underlying_realized_r": lifecycle.get("realized_r"),
                }
        return None

    async def _evaluate_candidate_active_trade(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
        quote: dict[str, Any],
    ) -> None:
        """Manage promoted C/D trades from their frozen underlying lifecycle."""
        runtime = self._candidate_runtime_for_trade(trade)
        if runtime is None:
            trade.option_data_status = (
                trade.option_data_status
                if trade.option_data_status not in {"", "ENTRY_CAPTURED"}
                else "AWAITING_STRATEGY_LIFECYCLE"
            )
            await self.repo.save_trade(trade)
            return

        try:
            current_r = float(runtime.get("current_r"))
        except (TypeError, ValueError):
            current_r = trade.current_r
        stop = runtime.get("current_underlying_stop")
        if stop is not None:
            trade.current_trailing_stop = float(stop)
        trade.current_r = round(current_r, 6)
        trade.peak_r = max(trade.peak_r, trade.current_r)

        entry = float(
            trade.underlying_entry_price or trade.entry_spot_price
        )
        risk = float(trade.underlying_r or trade.initial_r_points)
        exit_reason = (
            trade.pending_exit_reason
            or runtime.get("underlying_exit_reason")
        )
        exit_price_raw = runtime.get("underlying_exit_price")
        if exit_price_raw is not None:
            current_underlying = float(exit_price_raw)
        elif risk > 0:
            current_underlying = (
                entry + trade.current_r * risk
                if trade.direction == TradeDirection.BULLISH
                else entry - trade.current_r * risk
            )
        else:
            current_underlying = (
                features.futures_price
                if trade.strategy == StrategyName.DI_CONTINUATION
                else features.spot_price
            )
        trade.current_spot_price = float(current_underlying)
        trade.underlying_current_price = float(current_underlying)

        quote_valid = (
            quote.get("status") == "VALID"
            and float(quote.get("bid") or 0.0) > 0
        )
        current_option = float(
            quote.get("ltp") or quote.get("bid") or 0.0
        )
        if quote_valid and current_option > 0:
            trade.current_option_price = current_option
            trade.unrealized_pnl = round(
                (current_option - trade.entry_option_price)
                * trade.quantity,
                2,
            )

        # LIVE catastrophe protection is broker-held and reconciled before
        # reaching this method. PAPER/SHADOW still need the equivalent local
        # emergency option stop.
        if (
            trade.mode != AutoTradingMode.LIVE
            and quote_valid
            and current_option > 0
            and current_option <= trade.option_hard_stop_price
        ):
            await self._close_trade(
                trade,
                features,
                max(
                    0.0,
                    round(
                        float(quote["bid"]) - self._paper_slippage(),
                        2,
                    ),
                ),
                OPTION_EMERGENCY_STOP,
                quote=quote,
            )
            return

        # D keeps its frozen 1.5R whole-lot scale-out. The underlying monitor
        # decides when T1 occurred; OMS owns the real option fill.
        if (
            trade.strategy == StrategyName.SR_MOMENTUM_BREAKOUT
            and runtime.get("scale_out_time")
            and not trade.t1_reached
            and not trade.pending_exit_reason
        ):
            trade.t1_reached = True
            original_quantity = int(
                trade.initial_quantity or trade.quantity
            )
            original_lots = (
                original_quantity // trade.lot_size
                if trade.lot_size > 0
                else 0
            )
            partial_lots = self.strategy_d_paper.manager.scale_out_lots(
                original_lots
            )
            if partial_lots > 0:
                trade.t1_exit_quantity = partial_lots * trade.lot_size
                scale_price = float(
                    runtime.get("scale_out_price")
                    or current_underlying
                )
                trade.t1_decision_underlying_price = scale_price
                trade.t1_decision_r = underlying_r_for_price(
                    trade.direction,
                    entry,
                    risk,
                    scale_price,
                )
                trade.t1_exit_pending = True
            else:
                trade.t1_exit_quantity = 0
                trade.t1_exit_pending = False

        if (
            trade.t1_exit_pending
            and trade.partial_exit_filled_quantity
            < trade.t1_exit_quantity
        ):
            if trade.mode == AutoTradingMode.LIVE:
                if quote_valid:
                    await self._submit_live_t1_partial_exit(
                        trade,
                        features,
                        quote,
                    )
                else:
                    await self.repo.save_trade(trade)
                return
            if not quote_valid:
                await self.repo.save_trade(trade)
                return
            trade.state = TradeLifecycleState.RUNNER_MODE
            await self._execute_strategy_a_partial_exit(
                trade,
                features,
                quote,
            )
            return

        if exit_reason:
            trade.pending_exit_reason = str(exit_reason)
            trade.underlying_exit_reason = str(exit_reason)
            exit_time = runtime.get("underlying_exit_time")
            trade.underlying_exit_time = (
                datetime.fromisoformat(
                    str(exit_time).replace("Z", "+00:00")
                )
                if exit_time
                else features.timestamp
            )
            trade.pending_underlying_exit_time = (
                trade.underlying_exit_time
            )
            trade.underlying_exit_price = float(
                runtime.get("underlying_exit_price")
                or current_underlying
            )

            if not quote_valid:
                await self.repo.save_trade(trade)
                return

            if trade.mode == AutoTradingMode.LIVE:
                if trade.partial_exit_order_id:
                    await self.repo.save_trade(trade)
                    return
                await self._submit_live_final_exit(
                    trade,
                    features,
                    quote,
                    str(exit_reason),
                )
                return

            sell_price = max(
                0.0,
                round(
                    float(quote["bid"]) - self._paper_slippage(),
                    2,
                ),
            )
            if sell_price <= 0:
                await self.repo.save_trade(trade)
                return
            await self._close_trade(
                trade,
                features,
                sell_price,
                str(exit_reason),
                quote=quote,
            )
            return

        await self.repo.save_trade(trade)

    async def _evaluate_active_trade(self, trade: ActiveTrade, features: MarketFeatures) -> None:
        """Evaluates active position stops, trailing updates, and thesis reversal score."""
        if trade.state == TradeLifecycleState.ENTRY_PENDING and not trade.entry_order_id:
            await self._log_decision("ORDER", trade.strategy.value, "Entry submission requires reconciliation; no confirmed order reference", {"trade_id": trade.trade_id})
            return
        if features.spot_price <= 0:
            features = features.model_copy(update={"spot_price": trade.current_spot_price, "closed_5m_time": None, "closed_5m_price": None})
        if (
            trade.entry_order_id
            and trade.state == TradeLifecycleState.ENTRY_PENDING
        ):
            order = await self.oms.get_order(trade.entry_order_id)
            if not order:
                return
            terminal = order.status.value in (
                "FILLED",
                "CANCELLED",
                "REJECTED",
                "RISK_REJECTED",
                "EXPIRED",
                "FAILED_SAFE",
            )
            gateway = getattr(self.hist_svc, "broker_gateway", None)

            # Persist cumulative partial-entry progress immediately. Assignment,
            # rather than addition, makes restart/replay idempotent.
            if order.filled_quantity > 0:
                filled_quantity = int(order.filled_quantity)
                if (
                    trade.filled_quantity != filled_quantity
                    or (
                        order.average_price > 0
                        and trade.entry_option_price != order.average_price
                    )
                ):
                    trade.filled_quantity = filled_quantity
                    if order.average_price > 0:
                        trade.entry_option_price = float(order.average_price)
                        trade.option_hard_stop_price = round(
                            float(order.average_price)
                            * (
                                1
                                - self.config.risk.option_hard_stop_pct
                                / 100
                            ),
                            2,
                        )
                    await self.repo.save_trade(trade)

            if not terminal:
                timed_out = (
                    utc_now() - trade.entry_time
                ).total_seconds() >= self.config.risk.entry_order_timeout_sec
                if (
                    (order.filled_quantity > 0 or timed_out)
                    and order.broker_order_id
                    and gateway
                ):
                    await gateway.cancel_order(
                        order.broker_order_id,
                        mode=order.trading_mode,
                    )
                return

            if order.filled_quantity <= 0:
                trade.state = TradeLifecycleState.CLOSED
                trade.exit_time = utc_now()
                trade.exit_reason = "ENTRY_UNFILLED_" + order.status.value
                if self._is_strategy_a(trade.strategy):
                    self.strategy_a.on_execution_rejected(
                        trade.exit_time,
                        trade.exit_reason,
                    )
                if trade.strategy == StrategyName.VOLATILITY_BREAKOUT:
                    self.strategy_b.reset(trade.exit_time)
                self._active_trades_cache = [
                    item
                    for item in self._active_trades_cache
                    if item.trade_id != trade.trade_id
                ]
                await self._save_runtime()
                await self.repo.save_trade(trade)
                return

            trade.filled_quantity = int(order.filled_quantity)
            trade.quantity = int(order.filled_quantity)
            trade.initial_quantity = int(order.filled_quantity)
            trade.remaining_quantity = int(order.filled_quantity)
            if order.average_price > 0:
                trade.entry_option_price = float(order.average_price)
                trade.option_hard_stop_price = round(
                    float(order.average_price)
                    * (
                        1
                        - self.config.risk.option_hard_stop_pct / 100
                    ),
                    2,
                )
            trade.state = TradeLifecycleState.OPEN_INITIAL_RISK
            await self.repo.save_trade(trade)
            await self._record_execution({
                "ledger_id": (
                    f"LIVE-ENTRY-{order.order_id}-{trade.filled_quantity}"
                ),
                "trade_id": trade.trade_id,
                "side": "BUY",
                "timestamp": (
                    getattr(order, "updated_at", None) or utc_now()
                ).isoformat(),
                "raw_bid": trade.entry_bid,
                "raw_ask": trade.entry_ask,
                "raw_ltp": trade.entry_ltp,
                "executable_price": trade.entry_option_price,
                "slippage_points": 0.0,
                "quantity": trade.filled_quantity,
                "source": "BROKER_FILL",
                "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                "reason": "LIVE_ENTRY_FILLED",
            })
            if self._is_strategy_a(trade.strategy):
                self.strategy_a.confirm_entry(
                    getattr(order, "updated_at", None) or features.timestamp
                )
                await self._save_runtime()
                await self._record_strategy_a_lifecycle_event(
                    trade,
                    features,
                    "ENTRY_OPENED",
                )

        if trade.mode == AutoTradingMode.LIVE and trade.filled_quantity > 0:
            protection_ready = await self._sync_live_protective_stop(
                trade,
                features,
            )
            if trade.state == TradeLifecycleState.CLOSED or not protection_ready:
                return

        if (
            trade.mode == AutoTradingMode.LIVE
            and trade.partial_exit_order_id
        ):
            partial_exit_ready = (
                await self._sync_live_t1_partial_exit(
                    trade,
                    features,
                )
            )
            if (
                trade.state == TradeLifecycleState.CLOSED
                or not partial_exit_ready
            ):
                return

        if trade.exit_order_id:
            order = await self.oms.get_order(trade.exit_order_id)
            if not order:
                return

            (
                delta_filled,
                incremental_price,
                cumulative_filled,
            ) = self._apply_exit_order_progress(trade, order)
            if delta_filled > 0:
                await self._record_execution({
                    "ledger_id": (
                        f"LIVE-EXIT-{order.order_id}-{cumulative_filled}"
                    ),
                    "trade_id": trade.trade_id,
                    "side": "SELL",
                    "timestamp": (
                        getattr(order, "updated_at", None) or utc_now()
                    ).isoformat(),
                    "raw_bid": None,
                    "raw_ask": None,
                    "raw_ltp": None,
                    "executable_price": (
                        incremental_price
                        or float(order.average_price or order.price or 0.0)
                    ),
                    "slippage_points": 0.0,
                    "quantity": delta_filled,
                    "source": "BROKER_FILL",
                    "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                    "reason": trade.pending_exit_reason or "LIVE_EXIT",
                })

            if (
                order.status.value == "FILLED"
                or trade.exit_filled_quantity >= trade.quantity
            ):
                await self.repo.save_trade(trade)
                await self._close_trade(
                    trade,
                    features,
                    trade.exit_proceeds / trade.quantity,
                    trade.pending_exit_reason,
                )
                return

            if order.status.value in (
                "CANCELLED",
                "REJECTED",
                "RISK_REJECTED",
                "EXPIRED",
                "FAILED_SAFE",
            ):
                trade.exit_order_id = None
                self._reset_exit_order_progress(trade)
                await self.repo.save_trade(trade)
            else:
                await self.repo.save_trade(trade)
                bid = await self._executable_bid(trade)
                gateway = getattr(self.hist_svc, "broker_gateway", None)
                if gateway and bid and order.broker_order_id:
                    if bid != order.price:
                        await gateway.modify_order(
                            order.broker_order_id,
                            price=bid,
                            mode=order.trading_mode,
                        )
                return

        quote = await self._resolve_option_quote(trade)
        self._apply_quote_to_trade(trade, quote)
        current_option_price = quote.get("ltp") or quote.get("bid")
        self.position_manager.bull_derivatives_threshold = self._active_overrides.bull_derivatives_score if self._active_overrides.bull_derivatives_score is not None else 2
        self.position_manager.bear_derivatives_threshold = self._active_overrides.bear_derivatives_score if self._active_overrides.bear_derivatives_score is not None else 2

        if self._is_strategy_a(trade.strategy):
            quote_valid = quote.get("status") == "VALID" and quote.get("bid") and float(quote["bid"]) > 0
            current_option_price = round(float(current_option_price), 2) if current_option_price and float(current_option_price) > 0 else None
            # A prior futures decision is authoritative. Never reevaluate it
            # merely because an option quote is still unavailable.
            if trade.pending_exit_reason:
                if not quote_valid:
                    await self.repo.save_trade(trade)
                    return
                sell_price = max(0.0, round(float(quote["bid"]) - self._paper_slippage(), 2))
                if sell_price <= 0:
                    await self.repo.save_trade(trade)
                    return
                if trade.mode == AutoTradingMode.LIVE:
                    await self._submit_live_final_exit(
                        trade,
                        features,
                        quote,
                        trade.pending_exit_reason,
                    )
                    return
                await self._close_trade(trade, features, sell_price, trade.pending_exit_reason, quote=quote)
                return

            previous_peak_r = trade.peak_r
            previous_state = trade.state
            updated_trade, underlying_event = self.position_manager.update_position(
                trade, current_option_price, features, as_of=features.timestamp
            )
            if updated_trade.peak_r >= self.position_manager.strategy_config.trailing_activation_r and previous_peak_r < self.position_manager.strategy_config.trailing_activation_r:
                await self._record_strategy_a_lifecycle_event(updated_trade, features, "TRAIL_ACTIVATED")
            if updated_trade.state == TradeLifecycleState.RUNNER_MODE and previous_state != TradeLifecycleState.RUNNER_MODE:
                await self._record_strategy_a_lifecycle_event(updated_trade, features, "RUNNER_MODE")
            if is_option_emergency_stop(underlying_event):
                trade.pending_exit_reason = OPTION_EMERGENCY_STOP
                trade.option_exit_reason = OPTION_EMERGENCY_STOP
                trade.underlying_exit_reason = OPTION_EMERGENCY_STOP_UNDERLYING_REASON
                trade.underlying_exit_time = None
                trade.underlying_exit_price = None
                trade.underlying_outcome_status = OPTION_EMERGENCY_STOP_OUTCOME_STATUS
                await self._record_strategy_a_lifecycle_event(trade, features, "OPTION_EMERGENCY_STOP_DECIDED", OPTION_EMERGENCY_STOP)
                if not quote_valid:
                    await self._record_strategy_a_lifecycle_event(trade, features, "OPTION_EXIT_PENDING", OPTION_EMERGENCY_STOP)
                    await self.repo.save_trade(trade)
                    return
                if trade.mode == AutoTradingMode.LIVE:
                    await self._submit_live_final_exit(
                        trade,
                        features,
                        quote,
                        OPTION_EMERGENCY_STOP,
                    )
                    return
                sell_price = max(0.0, round(float(quote["bid"]) - self._paper_slippage(), 2))
                if sell_price <= 0:
                    await self._record_strategy_a_lifecycle_event(trade, features, "OPTION_EXIT_PENDING", OPTION_EMERGENCY_STOP)
                    await self.repo.save_trade(trade)
                    return
                await self._close_trade(trade, features, sell_price, OPTION_EMERGENCY_STOP, quote=quote)
                return
            if underlying_event == "T1_PARTIAL_EXIT":
                if trade.partial_exit_reason != "T1_REACHED_PARTIAL_EXIT":
                    await self._record_strategy_a_lifecycle_event(trade, features, "T1_REACHED", "T1_PARTIAL_EXIT")
                if quote_valid:
                    if trade.mode == AutoTradingMode.LIVE:
                        await self._submit_live_t1_partial_exit(
                            trade,
                            features,
                            quote,
                        )
                    else:
                        await self._execute_strategy_a_partial_exit(
                            trade,
                            features,
                            quote,
                        )
                else:
                    trade.t1_exit_pending = True
                    await self.repo.save_trade(trade)
                return
            if underlying_event == "T1_REACHED_NO_PARTIAL_ONE_LOT":
                await self._record_strategy_a_lifecycle_event(trade, features, "T1_REACHED", underlying_event)
                await self.repo.save_trade(trade)
                return
            if underlying_event:
                trade.pending_exit_reason = underlying_event
                trade.underlying_exit_reason = underlying_event
                trade.underlying_exit_time = features.timestamp
                trade.pending_underlying_exit_time = features.timestamp
                trade.underlying_exit_price = features.futures_price if features.futures_price > 0 else trade.underlying_current_price
                await self._record_strategy_a_lifecycle_event(trade, features, "UNDERLYING_EXIT_DECIDED", underlying_event)
                if not quote_valid:
                    await self._record_strategy_a_lifecycle_event(trade, features, "OPTION_EXIT_PENDING", underlying_event)
                    await self.repo.save_trade(updated_trade)
                    return
                if trade.mode == AutoTradingMode.LIVE:
                    await self._submit_live_final_exit(
                        trade,
                        features,
                        quote,
                        underlying_event,
                    )
                    return
                sell_price = max(0.0, round(float(quote["bid"]) - self._paper_slippage(), 2))
                if sell_price <= 0:
                    await self._record_strategy_a_lifecycle_event(trade, features, "OPTION_EXIT_PENDING", underlying_event)
                    await self.repo.save_trade(trade)
                    return
                await self._close_trade(updated_trade, features, sell_price, underlying_event, quote=quote)
                return
            await self.repo.save_trade(updated_trade)
            return

        if self._is_candidate_execution_strategy(trade.strategy):
            await self._evaluate_candidate_active_trade(
                trade,
                features,
                quote,
            )
            return

        # Strategy B preserves its established quote-gated management path.
        if quote.get("status") != "VALID" or not current_option_price or float(current_option_price) <= 0:
            await self.repo.save_trade(trade)
            return
        current_option_price = round(float(current_option_price), 2)
        updated_trade, exit_reason = self.position_manager.update_position(
            trade, current_option_price, features, as_of=features.timestamp
        )

        if exit_reason == "T1_PARTIAL_EXIT":
            if trade.mode == AutoTradingMode.LIVE:
                await self._submit_live_t1_partial_exit(
                    trade,
                    features,
                    quote,
                )
            else:
                await self._execute_strategy_a_partial_exit(
                    trade,
                    features,
                    quote,
                )
            return
        if exit_reason == "T1_REACHED_NO_PARTIAL_ONE_LOT":
            if self._is_strategy_a(trade.strategy):
                await self._record_strategy_a_lifecycle_event(
                    trade,
                    features,
                    "T1_REACHED_NO_PARTIAL_ONE_LOT",
                    exit_reason,
                )
            await self.repo.save_trade(trade)
            return

        exit_reason = trade.pending_exit_reason or exit_reason
        if exit_reason:
            if trade.mode == AutoTradingMode.LIVE:
                if self._is_strategy_a(trade.strategy) and trade.selected_contract_snapshot and not self._live_orders_enabled():
                    trade.option_data_status = "LIVE_TRADING_DISABLED"
                    await self.repo.save_trade(trade)
                    return
                if is_option_emergency_stop(exit_reason):
                    # The broker-held SL-limit is already the emergency exit.
                    # Do not cancel it and race a second SELL against the same
                    # position.
                    trade.pending_exit_reason = OPTION_EMERGENCY_STOP
                    trade.option_exit_reason = OPTION_EMERGENCY_STOP
                    await self.repo.save_trade(trade)
                    return
                if not await self._cancel_live_protective_stop_for_exit(
                    trade,
                    features,
                ):
                    trade.pending_exit_reason = exit_reason
                    await self.repo.save_trade(trade)
                    return
                bid = quote.get("bid")
                if not bid or float(bid) <= 0:
                    trade.option_data_status = "INVALID"
                    trade.option_data_quality_reasons.append("missing bid") if "missing bid" not in trade.option_data_quality_reasons else None
                    await self.repo.save_trade(trade)
                    return
                order_price = round(float(bid) - self._paper_slippage(), 2)
                intent = OrderIntent(
                    correlation_id=trade.trade_id, strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
                    source=SourceType.STRATEGY, instrument_id=trade.contract_instrument_id,
                    symbol=trade.contract_symbol, side=OrderSide.SELL, order_type=OrderType.LIMIT,
                    quantity=trade.quantity-trade.exit_filled_quantity, price=order_price,
                    product=ProductType.OPTIONS, trading_mode=TradingMode.LIVE,
                    reduce_only=True)
                order = await self.oms.create_order_intent(intent)
                trade.exit_order_id = order.order_id
                self._reset_exit_order_progress(trade)
                trade.pending_exit_reason = exit_reason
                trade.state = TradeLifecycleState.EXIT_PENDING
                await self.repo.save_trade(trade)
            else:
                sell_price = round(float(quote["bid"]) - self._paper_slippage(), 2)
                await self._close_trade(trade, features, sell_price, exit_reason, quote=quote)
        else:
            await self.repo.save_trade(updated_trade)

    async def _execute_strategy_a_partial_exit(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
        quote: dict[str, Any],
    ) -> None:
        quantity = trade.t1_exit_quantity
        if quantity <= 0 or quantity % trade.lot_size != 0:
            raise ValueError("Partial exit quantity must be a positive whole-lot quantity")
        raw_bid = float(quote.get("bid") or 0)
        slippage = self._paper_slippage()
        price = max(0.0, round(raw_bid - slippage, 2))
        if raw_bid <= 0 or price <= 0:
            trade.t1_exit_pending = True
            await self.repo.save_trade(trade)
            return
        self.position_manager.apply_t1_partial_fill(
            trade,
            raw_bid=raw_bid,
            executable_price=price,
            slippage_points=slippage,
            filled_at=features.timestamp,
        )
        await self._record_execution({
            "trade_id": trade.trade_id,
            "side": "SELL",
            "timestamp": features.timestamp.isoformat(),
            "raw_bid": raw_bid,
            "raw_ask": quote.get("ask"),
            "raw_ltp": quote.get("ltp"),
            "executable_price": price,
            "slippage_points": slippage,
            "quantity": quantity,
            "source": quote.get("source", "UNKNOWN"),
            "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
            "reason": "T1_REACHED_PARTIAL_EXIT",
        })
        if self._is_strategy_a(trade.strategy):
            await self._record_strategy_a_lifecycle_event(
                trade,
                features,
                "PARTIAL_EXIT",
                "T1_REACHED_PARTIAL_EXIT",
            )
        await self._log_decision(
            "EXIT",
            trade.strategy.value,
            f"{trade.strategy.value} T1 partial exit",
            trade.model_dump(mode="json"),
        )
        await self.repo.save_trade(trade)

    async def _close_trade(self, trade, features, price, reason, quote: Optional[dict[str, Any]] = None):
        emergency_option_stop = is_option_emergency_stop(reason)
        if emergency_option_stop:
            reason = OPTION_EMERGENCY_STOP
        trade.state = TradeLifecycleState.CLOSED
        trade.exit_time = utc_now()
        trade.exit_option_price = price
        exit_underlying = (
            trade.underlying_exit_price
            or (features.futures_price if features and features.futures_price > 0 else trade.underlying_current_price or trade.current_spot_price)
        )
        trade.exit_spot_price = None if emergency_option_stop else exit_underlying
        trade.underlying_exit_price = None if emergency_option_stop else exit_underlying
        trade.exit_reason = reason
        trade.option_exit_time = trade.exit_time
        trade.option_exit_reason = reason
        if emergency_option_stop:
            trade.underlying_exit_reason = OPTION_EMERGENCY_STOP_UNDERLYING_REASON
            trade.underlying_exit_time = None
            trade.underlying_outcome_status = OPTION_EMERGENCY_STOP_OUTCOME_STATUS
        else:
            trade.underlying_exit_reason = trade.underlying_exit_reason or reason
            trade.underlying_exit_time = trade.underlying_exit_time or trade.exit_time

        raw_entry = float(trade.entry_raw_ask or trade.entry_option_price)
        raw_exit = float((quote or {}).get("bid") or price)
        partial_quantity = trade.partial_exit_filled_quantity
        partial_price = trade.partial_exit_price or 0.0
        partial_raw_price = trade.partial_exit_raw_bid or partial_price
        original_quantity = trade.initial_quantity or (trade.quantity + partial_quantity)
        final_quantity = max(0, trade.quantity)
        trade.final_exit_quantity = final_quantity
        trade.execution_order_count = 1 + (1 if partial_quantity > 0 else 0) + (1 if final_quantity > 0 else 0)
        trade.raw_gross_option_pnl = round(
            (raw_exit - raw_entry) * final_quantity
            + (partial_raw_price - raw_entry) * partial_quantity,
            2,
        )
        trade.gross_pnl = round(
            (price - trade.entry_option_price) * final_quantity
            + (partial_price - trade.entry_option_price) * partial_quantity,
            2,
        )
        entry_slip = abs(float(trade.entry_executable_price or trade.entry_option_price) - raw_entry) * original_quantity
        partial_slip = (abs(partial_price - float(trade.partial_exit_raw_bid)) * partial_quantity
                        if partial_quantity and trade.partial_exit_raw_bid else 0.0)
        final_slip = abs(raw_exit - float(price)) * final_quantity
        trade.slippage_cost = round(entry_slip + partial_slip + final_slip, 2)
        turnover = (
            float(trade.entry_option_price) * original_quantity
            + partial_price * partial_quantity
            + float(price) * final_quantity
        )
        buy_turnover = float(trade.entry_option_price) * original_quantity
        sell_turnover = float(price) * trade.quantity + float(partial_price) * partial_quantity
        r = self.config.risk
        trade.brokerage = round(trade.execution_order_count * r.paper_brokerage_per_order, 2)
        trade.exchange_charges = round(turnover * r.paper_exchange_charge_rate, 2)
        trade.stt = round(sell_turnover * r.paper_stt_sell_rate, 2)
        trade.sebi_charges = round(turnover * r.paper_sebi_charge_rate, 2)
        trade.stamp_duty = round(buy_turnover * r.paper_stamp_buy_rate, 2)
        trade.gst = round((trade.brokerage + trade.exchange_charges + trade.sebi_charges) * r.paper_gst_rate, 2)
        trade.transaction_costs = round(
            trade.brokerage + trade.exchange_charges + trade.stt + trade.gst + trade.sebi_charges + trade.stamp_duty,
            2,
        )
        trade.net_pnl = round(trade.gross_pnl - trade.transaction_costs, 2)
        trade.return_on_premium_pct = round((trade.net_pnl / buy_turnover) * 100, 4) if buy_turnover else None
        trade.cost_assumption_version = r.paper_cost_assumption_version
        underlying_entry = trade.underlying_entry_price or trade.entry_spot_price
        risk_points = trade.underlying_r or trade.initial_r_points
        exits: list[tuple[int, float]] = []
        if partial_quantity and trade.t1_decision_underlying_price:
            exits.append((partial_quantity, trade.t1_decision_underlying_price))
            trade.t1_realized_r = underlying_r_for_price(
                trade.direction, underlying_entry, risk_points, trade.t1_decision_underlying_price
            )
        if final_quantity and exit_underlying and not emergency_option_stop:
            exits.append((final_quantity, exit_underlying))
            trade.runner_realized_r = underlying_r_for_price(
                trade.direction, underlying_entry, risk_points, exit_underlying
            )
        trade.realized_r = None if emergency_option_stop else calculate_realized_trade_r(
            trade.direction, underlying_entry, risk_points, original_quantity, exits
        )
        if trade.gross_pnl < 0:
            self._last_loss_exit_time = trade.exit_time
        if self._is_strategy_a(trade.strategy):
            self.strategy_a.on_exit(trade.direction, trade.exit_time)
        if trade.strategy == StrategyName.VOLATILITY_BREAKOUT:
            self.strategy_b.reset(trade.exit_time)
        self._active_trades_cache = [t for t in self._active_trades_cache if t.trade_id != trade.trade_id]
        await self._save_runtime()
        await self.repo.save_trade(trade)
        if self._is_strategy_a(trade.strategy):
            await self._record_strategy_a_lifecycle_event(trade, features, "CLOSED", reason)
        if trade.mode != AutoTradingMode.LIVE:
            await self._record_execution({
                "trade_id": trade.trade_id,
                "side": "SELL",
                "timestamp": trade.exit_time.isoformat(),
                "raw_bid": (quote or {}).get("bid"),
                "raw_ask": (quote or {}).get("ask"),
                "raw_ltp": (quote or {}).get("ltp"),
                "executable_price": price,
                "slippage_points": self._paper_slippage(),
                "quantity": final_quantity,
                "source": (quote or {}).get("source", "UNKNOWN"),
                "cost_assumption_version": r.paper_cost_assumption_version,
                "reason": reason,
            })
        await self._log_decision("EXIT", trade.strategy.value, "Position closed: " + str(reason), trade.model_dump(mode="json"))
        await self.bus.publish(EventEnvelope(topic=Topics.STRATEGY_SIGNAL, payload={"event": "TRADE_CLOSED", "trade": trade.model_dump(mode="json")}))

    async def manual_exit_trade(self, trade_id: str, reason: str = "MANUAL_UI_EXIT") -> Optional[ActiveTrade]:
        """Manually exit an active trade immediately.
        
        In Paper mode, immediately marks the trade CLOSED at the latest market-tracking price.
        In Live mode, submits a limit/market sell order intent to OMS.
        """
        active_trades = await self.repo.get_active_trades()
        target = next((t for t in active_trades if t.trade_id == trade_id), None)
        if not target:
            # Check if it was already closed
            trades = await self.repo.list_trades(limit=50)
            return next((t for t in trades if t.trade_id == trade_id), None)

        features = self._last_features or await self._gather_features()
        quote = await self._resolve_option_quote(target)
        self._apply_quote_to_trade(target, quote)
        if quote.get("status") != "VALID" or not quote.get("bid") or float(quote.get("bid")) <= 0:
            # Compatibility for legacy manually-created records that predate
            # selected-contract capture. Forward-validation trades always have
            # selected_contract_snapshot and are never synthetically filled.
            if target.mode == AutoTradingMode.PAPER and not target.selected_contract_snapshot and not target.signal_id:
                await self._close_trade(target, features, target.current_option_price, reason, quote={
                    "bid": target.current_option_price, "source": "LEGACY_MANUAL_RECORD"
                })
                return target
            await self.repo.save_trade(target)
            return target
        exit_price = round(float(quote["bid"]) - self._paper_slippage(), 2)

        if target.mode in (AutoTradingMode.PAPER, AutoTradingMode.SHADOW_ONLY):
            await self._close_trade(target, features, exit_price, reason, quote=quote)
            return target
        else:
            if not await self._cancel_live_protective_stop_for_exit(
                target,
                features,
            ):
                target.pending_exit_reason = reason
                await self.repo.save_trade(target)
                return target
            intent = OrderIntent(
                correlation_id=target.trade_id,
                strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
                source=SourceType.STRATEGY,
                instrument_id=target.contract_instrument_id,
                symbol=target.contract_symbol,
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                quantity=target.quantity - target.exit_filled_quantity,
                price=exit_price,
                product=ProductType.OPTIONS,
                trading_mode=TradingMode.LIVE,
                reduce_only=True,
            )
            order = await self.oms.create_order_intent(intent)
            target.exit_order_id = order.order_id
            self._reset_exit_order_progress(target)
            target.pending_exit_reason = reason
            target.state = TradeLifecycleState.EXIT_PENDING
            await self.repo.save_trade(target)
            return target

    # --- Market Data & Chain Fetching ---
    def _active_broker_context(self) -> tuple[str, Any | None, bool]:
        """Return the configured frequent market-data provider context."""
        gateway = (
            getattr(self.chain_svc, "broker_gateway", None)
            or getattr(self.hist_svc, "broker_gateway", None)
        )
        if not gateway:
            return "unknown", None, False

        provider = str(
            getattr(
                gateway,
                "frequent_data_broker_name",
                getattr(gateway, "active_broker_name", "unknown"),
            )
            or "unknown"
        ).lower()
        adapter = getattr(
            gateway,
            "frequent_data_adapter",
            getattr(gateway, "active_adapter", None),
        )
        if hasattr(gateway, "is_broker_active") and provider in {"breeze", "kite"}:
            active = bool(gateway.is_broker_active(provider))
        elif provider == "breeze":
            client = getattr(adapter, "client_manager", None)
            active = bool(client and getattr(client, "is_active", False))
        else:
            active = bool(adapter and getattr(adapter, "is_active", False))
        return provider, adapter, active

    async def _resolve_strategy_a_futures_instrument(self) -> Optional[str]:
        inst_svc = getattr(self.chain_svc, "inst_svc", None)
        provider, adapter, active = self._active_broker_context()
        self._market_data_status.update({"provider": provider, "provider_active": active})
        if not inst_svc:
            self._market_data_status["last_error"] = "INSTRUMENT_SERVICE_UNAVAILABLE"
            return None
        resolver = getattr(adapter, "resolve_nearest_future", None)
        if active and callable(resolver):
            try:
                contract = await resolver("NIFTY")
            except Exception as exc:
                logger.exception("%s futures contract resolution failed", provider)
                contract = None
                self._market_data_status["last_error"] = f"FUTURES_CONTRACT_RESOLUTION_FAILED:{type(exc).__name__}"
            if contract:
                instrument = await inst_svc.upsert_futures_contract(
                    underlying=str(contract.get("underlying") or "NIFTY"),
                    expiry=str(contract["expiry"]),
                    stock_code=str(contract.get("stock_code") or contract.get("symbol") or "NIFTY"),
                    broker=str(contract.get("broker") or provider.upper()),
                    exchange=str(contract.get("exchange") or "NFO"),
                    lot_size=int(contract.get("lot_size") or 1),
                    tick_size=float(contract.get("tick_size") or 0.05),
                    broker_token=str(contract.get("broker_token") or "") or None,
                )
                self._market_data_status.update({"futures_instrument": instrument.instrument_id, "last_error": None})
                return instrument.instrument_id
        if provider == "breeze" and active:
            fallback = await inst_svc.ensure_current_nifty_futures()
            if not fallback and getattr(inst_svc, "repo", None):
                fallback = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
            active_id = resolve_active_futures_instrument(fallback, as_of=utc_now())
            if active_id:
                logger.warning("Using calendar fallback for Breeze futures contract: %s", active_id)
                self._market_data_status.update({"futures_instrument": active_id, "last_error": "BROKER_CONTRACT_DISCOVERY_FALLBACK"})
                return active_id
        if not active and provider in {"breeze", "kite"}:
            self._market_data_status["last_error"] = "BROKER_SESSION_INACTIVE"
        else:
            self._market_data_status["last_error"] = self._market_data_status.get("last_error") or "FUTURES_CONTRACT_UNAVAILABLE"
        return None

    async def _gather_features(self) -> MarketFeatures:
        candles_5m = await self._get_recent_candles("5m")
        candles_15m = await self._get_recent_candles("15m")
        futures: list[Candle] = []
        futures_5m: list[Candle] = []
        active_instrument = await self._resolve_strategy_a_futures_instrument()
        if active_instrument:
            futures = await self._get_recent_candles("15m", active_instrument)
            futures_5m = await self._get_recent_candles("5m", active_instrument)
            logger.info("Strategy A futures history: provider=%s instrument=%s interval=15m candles=%d",
                        self._market_data_status.get("provider"), active_instrument, len(futures))
            logger.info("Strategy E futures history: provider=%s instrument=%s interval=5m candles=%d",
                        self._market_data_status.get("provider"), active_instrument, len(futures_5m))
        self._market_data_status.update({
            "futures_instrument": active_instrument,
            "futures_candle_count": len(futures),
            "latest_futures_candle": futures[-1].end_time.isoformat() if futures else None,
        })
        if active_instrument and not futures:
            self._market_data_status["last_error"] = "FUTURES_HISTORY_UNAVAILABLE"
        elif futures:
            self._market_data_status["last_error"] = None
        self._market_snapshot = (candles_5m, candles_15m, futures)
        self._strategy_e_futures_5m = futures_5m

        now = utc_now()
        spot_5m_age = (
            max(0.0, (now - candles_5m[-1].end_time).total_seconds())
            if candles_5m
            else None
        )
        futures_15m_age = (
            max(0.0, (now - futures[-1].end_time).total_seconds())
            if futures
            else None
        )
        futures_5m_age = (
            max(0.0, (now - futures_5m[-1].end_time).total_seconds())
            if futures_5m
            else None
        )
        try:
            max_quote_age = float(
                get_platform_settings().live_market_data_max_age_seconds
            )
        except Exception:
            max_quote_age = 5.0
        execution_feed_health = (
            self.mkt_svc.get_execution_feed_health(
                max_age_seconds=max_quote_age,
            )
            if self.mkt_svc is not None
            else {
                "healthy": False,
                "status": "BLOCKED",
                "reasons": ["MARKET_DATA_SERVICE_UNAVAILABLE"],
            }
        )
        self._market_data_status.update({
            "execution_feed_healthy": bool(
                execution_feed_health.get("healthy")
            ),
            "execution_feed_status": execution_feed_health.get("status"),
            "execution_feed_reasons": execution_feed_health.get(
                "reasons",
                [],
            ),
            "execution_feed_checked_at": execution_feed_health.get(
                "checked_at"
            ),
            "execution_feed_max_age_seconds": max_quote_age,
            "latest_spot_5m_candle_age_seconds": (
                round(spot_5m_age, 3)
                if spot_5m_age is not None
                else None
            ),
            "latest_futures_15m_candle_age_seconds": (
                round(futures_15m_age, 3)
                if futures_15m_age is not None
                else None
            ),
            "latest_futures_5m_candle_age_seconds": (
                round(futures_5m_age, 3)
                if futures_5m_age is not None
                else None
            ),
            "strategy_a_signal_data_fresh": bool(
                futures_15m_age is not None
                and futures_15m_age <= 1200.0
            ),
            "strategy_b_signal_data_fresh": bool(
                spot_5m_age is not None
                and spot_5m_age <= 600.0
            ),
            "strategy_e_signal_data_fresh": bool(
                futures_5m_age is not None
                and futures_5m_age <= 600.0
            ),
        })

        chain = await self._get_option_chain()
        spot = candles_5m[-1].close if candles_5m else 0.0
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote("INST-NIFTY-INDEX")
            if q and getattr(q, "source", "UNKNOWN") in ("BREEZE", "KITE", "LIVE") and q.last_price > 0 and 0 <= (utc_now()-q.timestamp).total_seconds() <= 30:
                spot = q.last_price
        return FeatureEngine.compute_all_features(
            candles_5m=candles_5m, candles_15m=candles_15m,
            futures_candles=futures, option_chain=chain, spot_price=spot)

    async def _get_recent_candles(self, interval: str, instrument_id="INST-NIFTY-INDEX") -> list[Candle]:
        if not self.hist_svc:
            return []
        try:
            candles = await self.hist_svc.get_candles(
                instrument_id=instrument_id,
                interval=interval,
                requested_source="MIXED",
                allow_provider_fallback=True,
                allow_synthetic_fallback=False,
            )
            now = utc_now()
            expected_sec = 900 if "15" in interval else 300
            return sorted({c.start_time: c for c in candles
                           if c.source in ("BREEZE", "KITE", "LIVE") and c.end_time <= now
                           and c.interval == interval
                           and abs((c.end_time - c.start_time).total_seconds() - expected_sec) <= 5
                           }.values(), key=lambda c: c.start_time)
        except Exception:
            logger.exception("Real candle retrieval failed: instrument=%s interval=%s", instrument_id, interval)
            return []

    async def _resolve_option_price(self, trade: ActiveTrade, features: Optional[MarketFeatures] = None) -> Optional[float]:
        """Compatibility helper returning only a real current option price."""
        quote = await self._resolve_option_quote(trade)
        self._apply_quote_to_trade(trade, quote)
        value = quote.get("ltp") or quote.get("bid")
        return round(float(value), 2) if value and float(value) > 0 and quote.get("status") == "VALID" else None

    async def _executable_bid(self, trade: ActiveTrade) -> Optional[float]:
        q = self.mkt_svc.get_latest_quote(trade.contract_instrument_id) if self.mkt_svc else None
        if q and getattr(q, "source", "UNKNOWN") in ("BREEZE", "KITE", "LIVE") and getattr(q, "best_bid", 0) > 0 and 0 <= (utc_now()-q.timestamp).total_seconds() <= 30:
            return q.best_bid
        if self.chain_svc:
            try:
                chain = await self.chain_svc.get_chain(underlying="NIFTY", expiry=trade.expiry)
                if chain.get("source") in ("BREEZE", "KITE", "LIVE"):
                    for strike in chain.get("strikes", []):
                        for side in ("call", "put"):
                            leg = strike.get(side) or {}
                            if leg.get("instrument_id") == trade.contract_instrument_id:
                                bid = float(leg.get("bid") or 0)
                                if bid > 0:
                                    return bid
            except Exception:
                logger.exception("Executable option bid refresh failed")
        return None

    async def _get_option_chain(self) -> dict[str, Any]:
        if self.chain_svc:
            try:
                chain = await self.chain_svc.get_chain(underlying="NIFTY")
                if chain.get("source") in ("BREEZE", "KITE", "LIVE"):
                    capabilities = (
                        chain.get("capabilities")
                        if isinstance(chain.get("capabilities"), dict)
                        else {}
                    )
                    strategy_a_ready = bool(
                        capabilities.get(
                            "strategy_a_contract_selection_ready",
                            capabilities.get("verified_delta_available", False),
                        )
                    )
                    self._market_data_status.update({
                        "option_chain_source": chain.get("source"),
                        "option_chain_captured_at": (
                            chain.get("captured_at")
                            or chain.get("timestamp")
                        ),
                        "strategy_a_option_execution_ready": (
                            strategy_a_ready
                        ),
                        "strategy_a_option_execution_reason": (
                            None
                            if strategy_a_ready
                            else capabilities.get(
                                "strategy_a_rejection_reason",
                                "VERIFIED_OPTION_DELTA_UNAVAILABLE",
                            )
                        ),
                    })
                    return chain
                # Offline/synthetic matrices remain usable by the UI, but may
                # never create a forward option-validation trade.
                if chain.get("strikes"):
                    self._market_data_status.update({
                        "option_chain_source": "UNAVAILABLE",
                        "strategy_a_option_execution_ready": False,
                        "strategy_a_option_execution_reason": (
                            "SYNTHETIC_OPTION_CHAIN_NOT_EXECUTABLE"
                        ),
                    })
                    return {
                        **chain,
                        "source": "UNAVAILABLE",
                        "validation_rejection": (
                            "synthetic option prices are not executable"
                        ),
                    }
            except Exception:
                logger.exception("Option-chain retrieval failed")
        self._market_data_status.update({
            "option_chain_source": "UNAVAILABLE",
            "strategy_a_option_execution_ready": False,
            "strategy_a_option_execution_reason": "OPTION_CHAIN_UNAVAILABLE",
        })
        return {
            "source": "UNAVAILABLE",
            "strikes": [],
            "capabilities": {
                "verified_delta_available": False,
                "verified_greeks_available": False,
                "strategy_a_contract_selection_ready": False,
                "strategy_a_rejection_reason": "OPTION_CHAIN_UNAVAILABLE",
            },
        }

    async def _capture_option_chain_snapshot(
        self,
        *,
        signal: StrategySignal,
        spot_price: float,
        chain: dict[str, Any],
        selector_candidates: list[dict[str, Any]],
        selected_contract: Any,
        rejection_reason: Optional[str],
        execution_mode: AutoTradingMode,
    ) -> None:
        """Persist the exact selector inputs observed at an ENTRY_READY event."""
        selected_right = "CE" if signal.option_type == OptionType.CALL else "PE"
        normalized_selector_candidates: list[dict[str, Any]] = []
        status_by_strike: dict[tuple[float, str], dict[str, Any]] = {}
        for candidate in selector_candidates:
            item = dict(candidate)
            item["right"] = selected_right
            item["premium"] = item.get("ask")
            item["eligible"] = item.get("status") == "ELIGIBLE"
            item["rejection_reason"] = None if item["eligible"] else item.get("status")
            normalized_selector_candidates.append(item)
            status_by_strike[(float(item["strike"]), selected_right)] = item

        candidate_strikes = {float(item["strike"]) for item in normalized_selector_candidates}
        chain_candidates: list[dict[str, Any]] = []
        for strike_row in chain.get("strikes", []):
            try:
                strike = float(strike_row["strike"])
            except (KeyError, TypeError, ValueError):
                continue
            if candidate_strikes and strike not in candidate_strikes:
                continue
            for leg_key, right in (("call", "CE"), ("put", "PE")):
                leg = strike_row.get(leg_key)
                if not leg:
                    continue
                bid = float(leg.get("bid", 0.0) or 0.0)
                ask = float(leg.get("ask", 0.0) or 0.0)
                mid = (bid + ask) / 2.0 if bid + ask > 0 else 0.0
                spread_pct = round((ask - bid) / mid * 100.0, 2) if mid > 0 else None
                selector_item = status_by_strike.get((strike, right))
                chain_candidates.append({
                    "strike": strike,
                    "right": right,
                    "instrument_id": leg.get("instrument_id"),
                    "instrument_token": leg.get("instrument_token") or leg.get("token") or leg.get("broker_token"),
                    "expiry": leg.get("expiry") or chain.get("expiry"),
                    "bid": bid,
                    "ask": ask,
                    "ltp": float(leg.get("ltp", 0.0) or 0.0),
                    "premium": ask,
                    "spread_pct": spread_pct,
                    "open_interest": int(leg.get("open_interest", 0) or 0),
                    "volume": int(leg.get("volume", 0) or 0),
                    "lot_size": int(leg.get("lot_size", 0) or 0),
                    "eligible": selector_item.get("eligible") if selector_item else None,
                    "rejection_reason": selector_item.get("rejection_reason") if selector_item else "NOT_EVALUATED_BY_DIRECTIONAL_SELECTOR",
                })

        captured_at = utc_now().isoformat()
        await self.repo.save_option_chain_snapshot({
            "snapshot_id": f"OPTCHAIN-{generate_id()}",
            "strategy_signal_id": signal.signal_id,
            "captured_at": captured_at,
            "selector_timestamp": utc_now().isoformat(),
            "chain_snapshot_timestamp": chain.get("captured_at") or captured_at,
            "signal_timestamp": signal.timestamp.isoformat(),
            "strategy": signal.strategy.value,
            "execution_mode": execution_mode.value,
            "direction": signal.direction.value,
            "spot_price": float(spot_price),
            "chain_spot_price": chain.get("spot_price"),
            "expiry": chain.get("expiry"),
            "source": chain.get("source", "UNAVAILABLE"),
            "selector_candidates": normalized_selector_candidates,
            "chain_candidates": chain_candidates,
            "selected_contract": selected_contract.model_dump(mode="json") if selected_contract else None,
            "selector_result": "SELECTED" if selected_contract else "REJECTED",
            "rejection_reason": rejection_reason,
        })

    async def _log_decision(
        self,
        category: str,
        strategy: Optional[str],
        message: str,
        details: dict[str, Any],
    ) -> None:
        entry = DecisionLogEntry(
            id=f"DEC-{int(utc_now().timestamp() * 1000)}",
            timestamp=utc_now(),
            category=category,
            strategy=strategy,
            message=message,
            details=details,
        )
        await self.repo.save_decision_log(entry)
        await self.bus.publish(
            EventEnvelope(topic=Topics.STRATEGY_SIGNAL, payload={"event": "DECISION_LOG", "log": entry.model_dump(mode="json")})
        )

    # --- Query & Diagnostic APIs ---
    def get_active_overrides(self) -> ThresholdOverrides:
        return self._active_overrides

    async def update_overrides(self, overrides: ThresholdOverrides) -> ThresholdOverrides:
        self._active_overrides = overrides
        self.strategy_b.reset(utc_now())
        await self._save_runtime()
        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message="Manual threshold overrides updated",
            details=overrides.model_dump(mode="json"),
        )
        return self._active_overrides

    async def reset_overrides(self) -> ThresholdOverrides:
        self._active_overrides = ThresholdOverrides()
        self.strategy_b.reset(utc_now())
        await self._save_runtime()
        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message="Manual threshold overrides reset to strategy defaults",
            details=self._active_overrides.model_dump(mode="json"),
        )
        return self._active_overrides

    def _candidate_monitor_diagnostics(
        self,
        *,
        strategy: StrategyName,
        label: str,
        status: dict[str, Any],
        enabled: bool,
        execution_mode: AutoTradingMode,
        current_price: float,
        active_direction: str | None,
        target_entry_level: float | None = None,
    ) -> list[StrategyTriggerDiagnostics]:
        """Adapt frozen C/D signal engines to the common executable-strategy UI."""
        raw_status = str(status.get("status") or "NOT_INITIALIZED")
        blocked = raw_status in {
            "DISABLED",
            "DISABLED_BY_CONFIG",
            "INITIALIZATION_FAILED",
            "OBSERVATION_FAILED",
            "DATA_RETRIEVAL_FAILED",
            "MARKET_DATA_UNAVAILABLE",
            "NATIVE_FUTURES_UNAVAILABLE",
            "NO_ACTIVE_FUTURES_DATA",
        }
        fingerprint_ok = bool(status.get("candidate_spec_fingerprint"))
        data_ready = enabled and not blocked
        execution_ready = bool(
            enabled
            and execution_mode != AutoTradingMode.DISABLED
            and (
                execution_mode != AutoTradingMode.LIVE
                or (
                    self.config.system_armed
                    and self._live_orders_enabled()
                )
            )
        )
        rows: list[StrategyTriggerDiagnostics] = []
        for option_type, direction in (
            (OptionType.CALL, TradeDirection.BULLISH),
            (OptionType.PUT, TradeDirection.BEARISH),
        ):
            is_active = active_direction == option_type.value
            conditions = [
                TriggerCondition(
                    id="strategy_enabled",
                    name="Strategy enabled",
                    current_value="ENABLED" if enabled else "DISABLED",
                    target_threshold="Enabled in strategy configuration",
                    status="PASSED" if enabled else "BLOCKED",
                    gap_description=(
                        "Strategy is enabled"
                        if enabled
                        else "Strategy disabled by configuration"
                    ),
                ),
                TriggerCondition(
                    id="frozen_candidate",
                    name="Frozen strategy fingerprint",
                    current_value="VALID" if fingerprint_ok else "MISSING",
                    target_threshold="Frozen spec must match",
                    status="PASSED" if fingerprint_ok else "BLOCKED",
                    gap_description=(
                        "Strategy fingerprint verified"
                        if fingerprint_ok
                        else "Strategy fingerprint unavailable"
                    ),
                ),
                TriggerCondition(
                    id="market_data",
                    name="Real market data",
                    current_value=raw_status,
                    target_threshold="Required native real-market candles available",
                    status="PASSED" if data_ready else "BLOCKED",
                    gap_description=(
                        "Signal engine is receiving its required market data"
                        if data_ready
                        else raw_status
                    ),
                ),
                TriggerCondition(
                    id="execution_mode",
                    name="Execution mode",
                    current_value=execution_mode.value,
                    target_threshold="PAPER/SHADOW or armed LIVE execution",
                    status="PASSED" if execution_ready else "BLOCKED",
                    gap_description=(
                        f"{execution_mode.value} execution path is ready"
                        if execution_ready
                        else (
                            "LIVE mode requires system arming and platform live-trading permission"
                            if execution_mode == AutoTradingMode.LIVE
                            else "Execution is disabled"
                        )
                    ),
                ),
                TriggerCondition(
                    id="strategy_signal",
                    name="Frozen setup signal",
                    current_value="ACTIVE" if is_active else "WAITING",
                    target_threshold=f"{option_type.value} strategy signal",
                    status="PASSED" if is_active else "PENDING",
                    gap_description=(
                        "Signal/lifecycle is active in the integrated strategy engine"
                        if is_active
                        else "Waiting for all frozen signal conditions"
                    ),
                ),
            ]
            passed = sum(item.status == "PASSED" for item in conditions)
            if not enabled:
                overall = "DISABLED"
                blocker = "Strategy disabled by configuration"
            elif blocked:
                overall = "BLOCKED"
                blocker = raw_status
            elif not execution_ready:
                overall = "BLOCKED"
                blocker = "Execution mode is not ready"
            elif is_active:
                overall = "ACTIVE"
                blocker = "Integrated signal/lifecycle is active"
            else:
                overall = "WAITING"
                blocker = "Waiting for strategy signal"
            rows.append(
                StrategyTriggerDiagnostics(
                    strategy=strategy,
                    strategy_label=f"{label} {option_type.value}",
                    direction=direction,
                    option_type=option_type,
                    overall_status=overall,
                    passed_count=passed,
                    total_count=len(conditions),
                    ready_pct=round(
                        100.0 * passed / len(conditions),
                        1,
                    ),
                    key_blocker=blocker,
                    target_entry_level=target_entry_level,
                    current_spot=float(current_price or 0.0),
                    phase_state=overall,
                    phase_summary={
                        "candidate_id": status.get("candidate_id"),
                        "candidate_spec_fingerprint": status.get(
                            "candidate_spec_fingerprint"
                        ),
                        "execution_mode": execution_mode.value,
                        "observer_mode": status.get("execution_mode"),
                        "live_trading_allowed": bool(
                            execution_mode == AutoTradingMode.LIVE
                            and execution_ready
                        ),
                        "monitor_status": raw_status,
                    },
                    conditions=conditions,
                )
            )
        return rows

    async def get_trigger_diagnostics(self) -> TriggerDiagnosticsResponse:
        """Gathers granular condition diagnostics across all strategies and session gates."""
        features = self._last_features or MarketFeatures(timestamp=utc_now(), data_reason="Awaiting first completed evaluation")
        candles_5m, candles_15m, futures_candles = self._market_snapshot

        diag_a = self.strategy_a.diagnose(features, candles_5m, candles_15m, overrides=self._active_overrides, futures_candles=futures_candles)
        diag_b = self.strategy_b.diagnose(features, candles_5m, overrides=self._active_overrides)

        now = utc_now()
        strategy_a_window = self.position_manager.is_within_strategy_a_entry_window(now)
        strategy_b_window = self.position_manager.is_within_entry_window()
        is_window = (
            strategy_a_window if self.config.tunables.trend_pullback_enabled and not self.config.tunables.volatility_breakout_enabled
            else strategy_b_window if self.config.tunables.volatility_breakout_enabled and not self.config.tunables.trend_pullback_enabled
            else strategy_a_window or strategy_b_window
        )
        bypass_win = self._active_overrides.bypass_entry_window
        effective_window = is_window or bypass_win

        active_count = len(self._active_trades_cache)
        max_pos = self.config.risk.max_concurrent_positions
        pos_blocked = active_count >= max_pos

        in_cooldown = False
        cooldown_remaining = 0.0
        if self._last_loss_exit_time:
            mins = (now - self._last_loss_exit_time).total_seconds() / 60.0
            if mins < self.config.risk.cooldown_after_loss_min:
                in_cooldown = True
                cooldown_remaining = round(self.config.risk.cooldown_after_loss_min - mins, 1)

        today_trades = await self.repo.list_trades(limit=50)
        today_str = now.strftime("%Y-%m-%d")
        today_count = sum(1 for t in today_trades if t.entry_time.strftime("%Y-%m-%d") == today_str)
        max_daily = self.config.risk.max_trades_per_day

        strategy_a_market_ready = (
            not self.config.tunables.trend_pullback_enabled
            or (
                bool(futures_candles)
                and bool(self._market_data_status.get("provider_active"))
            )
        )
        strategy_a_market_reason = None
        if not strategy_a_market_ready:
            strategy_a_market_reason = str(
                self._market_data_status.get("last_error")
                or "NO_FUTURES_MARKET_DATA"
            )

        primary = "All system gates clear — monitoring live market candles for technical trigger"
        if self.config.kill_switch:
            primary = "Emergency Kill Switch is ACTIVE"
        elif not self.config.auto_trade_enabled:
            primary = "Auto-Trading Execution is DISABLED"
        elif not strategy_a_market_ready:
            primary = f"Strategy A market data unavailable: {strategy_a_market_reason}"
        elif not effective_window:
            primary = "Outside strategy entry window (Strategy A 09:45-14:45; Strategy B legacy schedule). Set 'Bypass Entry Window' in Overrides to test now."
        elif pos_blocked:
            primary = f"Max concurrent positions reached ({active_count}/{max_pos})"
        elif in_cooldown:
            primary = f"Post-loss cooldown active ({cooldown_remaining}m remaining)"
        elif today_count >= max_daily:
            primary = f"Daily trade limit reached ({today_count}/{max_daily})"
        elif self.config.mode == AutoTradingMode.LIVE and not self.config.system_armed:
            primary = "System set to LIVE mode but NOT ARMED"

        gates = GateBlockers(
            kill_switch_active=self.config.kill_switch,
            auto_trade_enabled=self.config.auto_trade_enabled,
            market_data_ready=strategy_a_market_ready,
            market_data_reason=strategy_a_market_reason,
            within_trading_window=effective_window,
            max_positions_reached=pos_blocked,
            in_cooldown=in_cooldown,
            daily_trades_count=today_count,
            daily_trades_max=max_daily,
            system_armed=self.config.system_armed,
            concurrent_positions_count=active_count,
            max_concurrent_positions=max_pos,
            can_enter_new_trades=bool(
                not self.config.kill_switch
                and self.config.auto_trade_enabled
                and strategy_a_market_ready
                and effective_window
                and not pos_blocked
                and not in_cooldown
                and today_count < max_daily
                and not (self.config.mode == AutoTradingMode.LIVE and not self.config.system_armed)
            ),
            primary_gate_reason=(
                "Ready"
                if primary.startswith("All system gates clear")
                else primary
            ),
            primary_blocker=primary,
        )

        c_integrated = next(
            (
                trade for trade in self._active_trades_cache
                if trade.strategy == StrategyName.DI_CONTINUATION
            ),
            None,
        )
        d_integrated = next(
            (
                trade for trade in self._active_trades_cache
                if trade.strategy == StrategyName.SR_MOMENTUM_BREAKOUT
            ),
            None,
        )
        c_active = (
            self._last_strategy_c_shadow_status.get("active_candidate_trade")
            or {}
        )
        c_direction = (
            c_integrated.option_type.value
            if c_integrated is not None
            else c_active.get("direction")
        )
        d_active = (
            self._last_strategy_d_paper_status.get("active_execution_trade")
            or self._last_strategy_d_paper_status.get("active_paper_trade")
            or {}
        )
        d_signal = d_active.get("signal") or {}
        d_direction = (
            d_integrated.option_type.value
            if d_integrated is not None
            else d_signal.get("option_type")
        )
        d_market = self._last_strategy_d_paper_status.get("market") or {}
        d_latest = self._last_strategy_d_paper_status.get("latest_signal") or {}

        diag_c = self._candidate_monitor_diagnostics(
            strategy=StrategyName.DI_CONTINUATION,
            label="Strategy C · DI Continuation",
            status=self._last_strategy_c_shadow_status,
            enabled=self.config.tunables.di_continuation_enabled,
            execution_mode=self._execution_mode_for_strategy(
                StrategyName.DI_CONTINUATION,
                OptionType.CALL,
            ),
            current_price=float(features.futures_price or 0.0),
            active_direction=c_direction,
        )
        diag_d = self._candidate_monitor_diagnostics(
            strategy=StrategyName.SR_MOMENTUM_BREAKOUT,
            label="Strategy D · S&R Momentum",
            status=self._last_strategy_d_paper_status,
            enabled=self.config.tunables.sr_momentum_breakout_enabled,
            execution_mode=self._execution_mode_for_strategy(
                StrategyName.SR_MOMENTUM_BREAKOUT,
                OptionType.CALL,
            ),
            current_price=float(
                d_market.get("spot_price")
                or features.spot_price
                or 0.0
            ),
            active_direction=d_direction,
            target_entry_level=(
                float(d_latest["breakout_level"])
                if d_latest.get("breakout_level") is not None
                else None
            ),
        )

        return TriggerDiagnosticsResponse(
            system_time=now,
            gates=gates,
            strategies=[*diag_a, *diag_b, *diag_c, *diag_d],
            active_overrides=self._active_overrides,
        )

    async def force_entry(
        self,
        strategy: StrategyName = StrategyName.TREND_PULLBACK,
        direction: TradeDirection = TradeDirection.BULLISH,
        option_type: Optional[OptionType] = None,
        override_premium_cap: Optional[float] = None,
    ) -> dict[str, Any]:
        """Manually force a non-Strategy-A trade setup entry immediately.

        Strategy A cannot be synthesized here because that would bypass its
        futures-only TrendPullback state machine and contaminate validation.
        """
        policy = self._execution_policy_for_strategy(strategy)
        if not policy.force_entry_allowed:
            return {
                "status": (
                    "STRATEGY_A_FORCE_ENTRY_DISABLED"
                    if strategy == StrategyName.TREND_PULLBACK
                    else "FROZEN_CANDIDATE_FORCE_ENTRY_DISABLED"
                    if strategy in {
                        StrategyName.DI_CONTINUATION,
                        StrategyName.SR_MOMENTUM_BREAKOUT,
                    }
                    else "EXECUTION_POLICY_FORCE_ENTRY_DISABLED"
                ),
                "reason": (
                    policy.live_block_reason
                    or "FORCE_ENTRY_DISABLED_BY_POLICY"
                ),
                "execution_policy": policy.to_dict(),
            }

        now = utc_now()
        # Manual force-entry must refresh the same runtime-health snapshot used
        # by automatic entries; cached features cannot authorize new exposure.
        features = await self._gather_features()
        entry_feed_healthy = bool(
            self._market_data_status.get("execution_feed_healthy")
        )
        signal_data_fresh = (
            bool(
                self._market_data_status.get(
                    "strategy_a_signal_data_fresh"
                )
            )
            if strategy == StrategyName.TREND_PULLBACK
            else bool(
                self._market_data_status.get(
                    "strategy_b_signal_data_fresh"
                )
            )
        )
        if not entry_feed_healthy or not signal_data_fresh:
            reasons = list(
                self._market_data_status.get(
                    "execution_feed_reasons",
                    [],
                )
            )
            if not signal_data_fresh:
                reasons.append(
                    "STALE_OR_MISSING_FUTURES_15M_CANDLE"
                    if strategy == StrategyName.TREND_PULLBACK
                    else "STALE_OR_MISSING_SPOT_5M_CANDLE"
                )
            return {
                "status": "ENTRY_DATA_UNHEALTHY",
                "reason": ";".join(dict.fromkeys(reasons)),
                "execution_policy": policy.to_dict(),
            }

        if strategy == StrategyName.TREND_PULLBACK:
            spot = features.futures_price if (features and features.futures_price > 0) else 0.0
            missing_reason = "Missing real futures price: Strategy A manual entry requires an authoritative futures/underlying price"
        else:
            spot = features.spot_price if (features and features.spot_price > 0) else 0.0
            missing_reason = "Missing real spot price: No current or historical NIFTY spot price available to price options and calculate risk"
        if strategy != StrategyName.TREND_PULLBACK and spot <= 0 and self.mkt_svc:
            q = self.mkt_svc.get_latest_quote("INST-NIFTY-INDEX")
            if q and q.last_price > 0:
                spot = q.last_price
        if strategy != StrategyName.TREND_PULLBACK and spot <= 0:
            candles_5m = await self._get_recent_candles("5m")
            if candles_5m:
                spot = candles_5m[-1].close

        if spot <= 0:
            return {
                "status": "DATA_UNAVAILABLE",
                "reason": missing_reason,
            }

        atr = max(10.0, features.atr_5m if (features and features.atr_5m > 0) else 15.0)

        if option_type is None:
            option_type = OptionType.CALL if direction == TradeDirection.BULLISH else OptionType.PUT

        # 1. Structural stop and R points
        if direction == TradeDirection.BULLISH:
            structural_stop = round(spot - (0.85 * atr), 2)
            r_points = round(spot - structural_stop, 2)
        else:
            structural_stop = round(spot + (0.85 * atr), 2)
            r_points = round(structural_stop - spot, 2)

        # 2. Select the execution contract after constructing the manual thesis.
        chain = await self._get_option_chain()
        cap = override_premium_cap or self._active_overrides.max_option_premium_cap
        selected_contract, candidates, rejection_reason = self.contract_selector.select_contract(
            direction=direction,
            spot_price=spot,
            option_chain=chain,
            override_premium_cap=cap,
            strategy_a=(strategy == StrategyName.TREND_PULLBACK),
            as_of=now,
        )

        if strategy == StrategyName.TREND_PULLBACK and chain.get("source") not in ("BREEZE", "KITE", "LIVE"):
            selected_contract = None
            rejection_reason = chain.get("validation_rejection", "NO_REAL_OPTION_QUOTE")

        if not selected_contract:
            await self._log_decision(
                category="FORCE_ENTRY",
                strategy=strategy.value,
                message=f"Forced entry failed: {rejection_reason}",
                details={"candidates_checked": len(candidates), "cap": cap or self.config.option_selection.max_option_premium},
            )
            return {"status": "CONTRACT_SELECTION_FAILED", "reason": rejection_reason, "candidates": candidates}

        # 3. Position Sizing
        if strategy == StrategyName.TREND_PULLBACK:
            sizing = self.risk_sizer.size(
                underlying_entry=spot,
                underlying_stop=structural_stop,
                option_delta=selected_contract.delta,
                lot_size=selected_contract.lot_size,
                option_entry=selected_contract.ask_price,
                account_equity=self.config.risk.account_equity,
            )
            lots, quantity = sizing.lots, sizing.quantity
        else:
            lots, quantity = self.position_manager.calculate_position_size(
                entry_premium=selected_contract.ask_price,
                account_equity=self.config.risk.account_equity,
                lot_size=selected_contract.lot_size,
            )

        if lots < 1:
            return {"status": "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"}

        execution_mode = self._execution_mode_for_strategy(strategy, option_type)

        # 4. Check LIVE Arming Gate for non-Strategy-A compatibility paths.
        if execution_mode == AutoTradingMode.LIVE and not self._live_orders_enabled():
            return {"status": "LIVE_TRADING_DISABLED"}
        if execution_mode == AutoTradingMode.LIVE and not self.config.system_armed:
            await self._log_decision(
                category="SECURITY",
                strategy=strategy.value,
                message="Forced LIVE order execution blocked: System is not ARMED",
                details={"symbol": selected_contract.symbol, "lots": lots, "quantity": quantity},
            )
            return {"status": "LIVE_SYSTEM_NOT_ARMED", "message": "System is in LIVE mode but not armed"}

        # 5. Create Active Trade
        trade_id = f"TRD-FORCED-{int(now.timestamp())}"
        entry_slippage = self._paper_slippage() if execution_mode in (AutoTradingMode.PAPER, AutoTradingMode.SHADOW_ONLY) else 0.0
        entry_price = round(selected_contract.ask_price + entry_slippage, 2)
        hard_stop_price = round(
            entry_price * (1.0 - (self.config.risk.option_hard_stop_pct / 100.0)),
            2,
        )

        new_trade = ActiveTrade(
            trade_id=trade_id,
            mode=execution_mode,
            strategy=strategy,
            direction=direction,
            option_type=option_type,
            contract_symbol=selected_contract.symbol,
            contract_instrument_id=selected_contract.instrument_id,
            expiry=selected_contract.expiry,
            strike=selected_contract.strike,
            quantity=quantity,
            lot_size=selected_contract.lot_size,
            lots=lots,
            entry_time=now,
            entry_option_price=entry_price,
            entry_spot_price=spot,
            underlying_entry_price=(spot if strategy == StrategyName.TREND_PULLBACK else None),
            underlying_current_price=(spot if strategy == StrategyName.TREND_PULLBACK else None),
            underlying_structural_stop=(structural_stop if strategy == StrategyName.TREND_PULLBACK else None),
            underlying_r=(r_points if strategy == StrategyName.TREND_PULLBACK else None),
            initial_quantity=quantity,
            remaining_quantity=quantity,
            initial_structural_stop=structural_stop,
            initial_r_points=r_points,
            current_option_price=selected_contract.ltp or selected_contract.ask_price,
            current_spot_price=spot,
            current_trailing_stop=structural_stop,
            option_hard_stop_price=hard_stop_price,
            current_r=0.0,
            peak_r=0.0,
            state=TradeLifecycleState.ENTRY_PENDING if execution_mode == AutoTradingMode.LIVE else TradeLifecycleState.OPEN_INITIAL_RISK,
            selected_contract_snapshot=selected_contract.model_dump(mode="json"),
            entry_bid=selected_contract.bid_price,
            entry_ask=selected_contract.ask_price,
            entry_ltp=selected_contract.ltp,
            entry_quote_source=chain.get("source"),
            entry_quote_timestamp=now,
            entry_quote_freshness_seconds=0.0,
            entry_slippage_points=entry_slippage,
            entry_raw_ask=selected_contract.ask_price,
            entry_executable_price=entry_price,
            current_bid=selected_contract.bid_price,
            current_ask=selected_contract.ask_price,
            current_ltp=selected_contract.ltp,
            current_quote_source=chain.get("source"),
            current_quote_timestamp=now,
            current_quote_freshness_seconds=0.0,
            current_quote_volume=selected_contract.volume,
            current_quote_open_interest=selected_contract.open_interest,
            cost_assumption_version=self.config.risk.paper_cost_assumption_version,
            cost_assumptions=self._cost_metadata(),
        )

        await self.repo.save_trade(new_trade)
        self._active_trades_cache.append(new_trade)

        if execution_mode != AutoTradingMode.LIVE:
            await self._record_execution({
                "trade_id": new_trade.trade_id,
                "side": "BUY",
                "timestamp": now.isoformat(),
                "raw_bid": selected_contract.bid_price,
                "raw_ask": selected_contract.ask_price,
                "raw_ltp": selected_contract.ltp,
                "executable_price": entry_price,
                "slippage_points": entry_slippage,
                "quantity": quantity,
                "source": chain.get("source", "UNKNOWN"),
                "cost_assumption_version": self.config.risk.paper_cost_assumption_version,
                "reason": "PAPER_OR_SHADOW_ENTRY",
            })

        await self._log_decision(
            category="ORDER",
            strategy=strategy.value,
            message=f"FORCED ENTRY TRIGGERED: {new_trade.mode.value} {new_trade.direction.value} {new_trade.contract_symbol} x {quantity} @ ₹{selected_contract.ask_price}",
            details=new_trade.model_dump(mode="json"),
        )

        # In LIVE mode, dispatch OrderIntent
        if execution_mode == AutoTradingMode.LIVE:
            intent = OrderIntent(
                intent_id=generate_id(),
                correlation_id=trade_id,
                strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
                source=SourceType.STRATEGY,
                instrument_id=selected_contract.instrument_id,
                symbol=selected_contract.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=selected_contract.ask_price,
                product=ProductType.OPTIONS,
                trading_mode=TradingMode.LIVE,
            )
            order = await self.oms.create_order_intent(intent)
            new_trade.entry_order_id = order.order_id
            new_trade.state = TradeLifecycleState.ENTRY_PENDING
            await self.repo.save_trade(new_trade)

        await self.bus.publish(
            EventEnvelope(
                topic=Topics.STRATEGY_SIGNAL,
                payload={"event": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")},
            )
        )

        return {"status": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")}

    async def get_status(self) -> dict[str, Any]:
        """Status payload consumed by the Auto-Trading UI."""
        # UI polling is read-only; only evaluate_cycle owns broker I/O.
        active_trades = await self.repo.get_active_trades()
        features = self._last_features or MarketFeatures(timestamp=utc_now(), data_reason="Awaiting first completed evaluation")

        # Position lifecycle updates run only inside evaluate_cycle().
        # Keeping this endpoint read-only prevents UI polling from racing the
        # scheduler or changing Strategy A state with a different snapshot.

        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades
        latest_signals = await self.repo.list_strategy_signals(limit=10)
        diagnostics = await self.get_trigger_diagnostics()

        strategy_a_trade = next(
            (
                trade for trade in active_trades
                if trade.strategy == StrategyName.TREND_PULLBACK
            ),
            None,
        )
        strategy_b_trade = next(
            (
                trade for trade in active_trades
                if trade.strategy == StrategyName.VOLATILITY_BREAKOUT
            ),
            None,
        )
        strategy_c_trade = next(
            (
                trade for trade in active_trades
                if trade.strategy == StrategyName.DI_CONTINUATION
            ),
            None,
        )
        strategy_d_trade = next(
            (
                trade for trade in active_trades
                if trade.strategy == StrategyName.SR_MOMENTUM_BREAKOUT
            ),
            None,
        )
        c_candidate = (
            self._last_strategy_c_shadow_status.get("active_candidate_trade")
            or {}
        )
        c_lifecycle = c_candidate.get("lifecycle") or {}
        c_paper = (
            self._last_strategy_c_shadow_status.get("active_paper_trade")
            or {}
        )
        d_paper = (
            self._last_strategy_d_paper_status.get("active_execution_trade")
            or self._last_strategy_d_paper_status.get("active_paper_trade")
            or {}
        )
        policy_a = self._execution_policy_for_strategy(
            StrategyName.TREND_PULLBACK
        )
        policy_b = self._execution_policy_for_strategy(
            StrategyName.VOLATILITY_BREAKOUT
        )
        policy_c = self._execution_policy_for_strategy(
            StrategyName.DI_CONTINUATION
        )
        policy_d = self._execution_policy_for_strategy(
            StrategyName.SR_MOMENTUM_BREAKOUT
        )

        gateway = (
            getattr(self.chain_svc, "broker_gateway", None)
            or getattr(self.hist_svc, "broker_gateway", None)
        )
        broker_routing = {
            "execution_broker": str(
                getattr(gateway, "execution_broker_name", "unknown")
                or "unknown"
            ).lower(),
            "frequent_data_broker": str(
                getattr(gateway, "frequent_data_broker_name", "unknown")
                or "unknown"
            ).lower(),
            "reference_data_broker": str(
                getattr(gateway, "reference_data_broker_name", "unknown")
                or "unknown"
            ).lower(),
        }

        return {
            "config": self.config.model_dump(mode="json"),
            "broker_routing": broker_routing,
            "execution_policy": self._execution_policy_matrix(),
            "scheduler": {
                "running": bool(self._is_running and self._loop_task and not self._loop_task.done()),
                "task_done": bool(self._loop_task.done()) if self._loop_task else None,
                "cycle_count": self._cycle_count,
                "last_cycle_status": self._last_cycle_status,
                "last_evaluation_time": (
                    self._last_eval_time.isoformat()
                    if self._last_eval_time > datetime.min.replace(tzinfo=timezone.utc)
                    else None
                ),
                "last_evaluation_age_seconds": (
                    round((utc_now() - self._last_eval_time).total_seconds(), 3)
                    if self._last_eval_time > datetime.min.replace(tzinfo=timezone.utc)
                    else None
                ),
                "evaluation_interval_seconds": max(1, self.config.tunables.evaluation_interval_sec),
            },
            "market_data": {**self._market_data_status, "last_evaluation_time": (self._last_eval_time.isoformat() if self._last_eval_time > datetime.min.replace(tzinfo=timezone.utc) else None)},
            "strategy_c_shadow": self._last_strategy_c_shadow_status,
            "strategy_c_paper": self._last_strategy_c_shadow_status,
            "strategy_d_paper": self._last_strategy_d_paper_status,
            "startup_reconciliation": self._startup_reconciliation,
            "live_reconciliation": self._last_live_reconciliation,
            "features": features.model_dump(mode="json"),
            "active_trades": [t.model_dump(mode="json") for t in active_trades],
            "signals": latest_signals,
            "strategies": {
                "trend_pullback": {
                    "enabled": self.config.tunables.trend_pullback_enabled,
                    "label": "Strategy A · Trend Pullback V3",
                    "state": (
                        strategy_a_trade.state.value
                        if strategy_a_trade is not None
                        else self.strategy_a.snapshot.state.value
                    ),
                    "execution_mode": (
                        strategy_a_trade.mode.value
                        if strategy_a_trade is not None
                        else policy_a.call_mode.value
                    ),
                    "effective_call_mode": policy_a.call_mode.value,
                    "effective_put_mode": policy_a.put_mode.value,
                    "promotion_state": policy_a.promotion_state,
                    "live_block_reason": policy_a.live_block_reason,
                    "force_entry_allowed": policy_a.force_entry_allowed,
                    "live_trading_allowed": policy_a.live_trading_allowed,
                    "current_r": (
                        strategy_a_trade.current_r
                        if strategy_a_trade is not None
                        else None
                    ),
                    "current_trailing_stop": (
                        strategy_a_trade.current_trailing_stop
                        if strategy_a_trade is not None
                        else None
                    ),
                    "broker_protective_stop_status": (
                        strategy_a_trade.protective_stop_status
                        if strategy_a_trade is not None
                        else None
                    ),
                    "broker_protective_stop_trigger": (
                        strategy_a_trade.protective_stop_trigger_price
                        if strategy_a_trade is not None
                        else None
                    ),
                    "broker_protective_stop_limit": (
                        strategy_a_trade.protective_stop_limit_price
                        if strategy_a_trade is not None
                        else None
                    ),
                    "active_trade_id": (
                        strategy_a_trade.trade_id
                        if strategy_a_trade is not None
                        else None
                    ),
                },
                "volatility_breakout": {
                    "enabled": self.config.tunables.volatility_breakout_enabled,
                    "label": "Strategy B · Volatility Breakout",
                    "state": (
                        strategy_b_trade.state.value
                        if strategy_b_trade is not None
                        else "SEARCHING"
                    ),
                    "execution_mode": (
                        strategy_b_trade.mode.value
                        if strategy_b_trade is not None
                        else policy_b.call_mode.value
                    ),
                    "effective_call_mode": policy_b.call_mode.value,
                    "effective_put_mode": policy_b.put_mode.value,
                    "promotion_state": policy_b.promotion_state,
                    "live_block_reason": policy_b.live_block_reason,
                    "force_entry_allowed": policy_b.force_entry_allowed,
                    "live_trading_allowed": policy_b.live_trading_allowed,
                    "current_r": (
                        strategy_b_trade.current_r
                        if strategy_b_trade is not None
                        else None
                    ),
                    "current_trailing_stop": (
                        strategy_b_trade.current_trailing_stop
                        if strategy_b_trade is not None
                        else None
                    ),
                    "broker_protective_stop_status": (
                        strategy_b_trade.protective_stop_status
                        if strategy_b_trade is not None
                        else None
                    ),
                    "broker_protective_stop_trigger": (
                        strategy_b_trade.protective_stop_trigger_price
                        if strategy_b_trade is not None
                        else None
                    ),
                    "broker_protective_stop_limit": (
                        strategy_b_trade.protective_stop_limit_price
                        if strategy_b_trade is not None
                        else None
                    ),
                    "active_trade_id": (
                        strategy_b_trade.trade_id
                        if strategy_b_trade is not None
                        else None
                    ),
                },
                "di_continuation": {
                    "enabled": self.config.tunables.di_continuation_enabled,
                    "label": "Strategy C · DI Continuation V1",
                    "state": (
                        strategy_c_trade.state.value
                        if strategy_c_trade is not None
                        else str(
                            self._last_strategy_c_shadow_status.get("status")
                            or "NOT_INITIALIZED"
                        )
                    ),
                    "execution_mode": (
                        strategy_c_trade.mode.value
                        if strategy_c_trade is not None
                        else policy_c.call_mode.value
                    ),
                    "effective_call_mode": policy_c.call_mode.value,
                    "effective_put_mode": policy_c.put_mode.value,
                    "promotion_state": policy_c.promotion_state,
                    "live_block_reason": policy_c.live_block_reason,
                    "force_entry_allowed": policy_c.force_entry_allowed,
                    "live_trading_allowed": policy_c.live_trading_allowed,
                    "candidate_id": self._last_strategy_c_shadow_status.get(
                        "candidate_id"
                    ),
                    "candidate_spec_fingerprint": (
                        self._last_strategy_c_shadow_status.get(
                            "candidate_spec_fingerprint"
                        )
                    ),
                    "paper_open_trades": self._last_strategy_c_shadow_status.get(
                        "paper_open_trades",
                        0,
                    ),
                    "paper_closed_trades": self._last_strategy_c_shadow_status.get(
                        "paper_closed_trades",
                        0,
                    ),
                    "paper_net_pnl": self._last_strategy_c_shadow_status.get(
                        "paper_net_pnl",
                        0.0,
                    ),
                    "current_r": (
                        strategy_c_trade.current_r
                        if strategy_c_trade is not None
                        else c_lifecycle.get("current_r")
                    ),
                    "current_trailing_stop": (
                        strategy_c_trade.current_trailing_stop
                        if strategy_c_trade is not None
                        else c_lifecycle.get("current_stop")
                    ),
                    "broker_protective_stop_status": (
                        strategy_c_trade.protective_stop_status
                        if strategy_c_trade is not None
                        else None
                    ),
                    "active_trade_id": (
                        strategy_c_trade.trade_id
                        if strategy_c_trade is not None
                        else c_paper.get("signal_id")
                    ),
                },
                "sr_momentum_breakout": {
                    "enabled": self.config.tunables.sr_momentum_breakout_enabled,
                    "label": "Strategy D · S&R Momentum V2",
                    "state": (
                        strategy_d_trade.state.value
                        if strategy_d_trade is not None
                        else str(
                            self._last_strategy_d_paper_status.get("status")
                            or "NOT_INITIALIZED"
                        )
                    ),
                    "execution_mode": (
                        strategy_d_trade.mode.value
                        if strategy_d_trade is not None
                        else policy_d.call_mode.value
                    ),
                    "effective_call_mode": policy_d.call_mode.value,
                    "effective_put_mode": policy_d.put_mode.value,
                    "promotion_state": policy_d.promotion_state,
                    "live_block_reason": policy_d.live_block_reason,
                    "force_entry_allowed": policy_d.force_entry_allowed,
                    "live_trading_allowed": policy_d.live_trading_allowed,
                    "candidate_id": self._last_strategy_d_paper_status.get(
                        "candidate_id"
                    ),
                    "candidate_spec_fingerprint": (
                        self._last_strategy_d_paper_status.get(
                            "candidate_spec_fingerprint"
                        )
                    ),
                    "paper_open_trades": self._last_strategy_d_paper_status.get(
                        "paper_open_trades",
                        0,
                    ),
                    "paper_closed_trades": self._last_strategy_d_paper_status.get(
                        "paper_closed_trades",
                        0,
                    ),
                    "paper_net_pnl": self._last_strategy_d_paper_status.get(
                        "paper_net_pnl",
                        0.0,
                    ),
                    "current_r": (
                        strategy_d_trade.current_r
                        if strategy_d_trade is not None
                        else d_paper.get("current_r")
                    ),
                    "current_trailing_stop": (
                        strategy_d_trade.current_trailing_stop
                        if strategy_d_trade is not None
                        else d_paper.get("current_underlying_stop")
                    ),
                    "broker_protective_stop_status": (
                        strategy_d_trade.protective_stop_status
                        if strategy_d_trade is not None
                        else None
                    ),
                    "active_trade_id": (
                        strategy_d_trade.trade_id
                        if strategy_d_trade is not None
                        else d_paper.get("signal_id")
                    ),
                },
            },
            "trigger_diagnostics": diagnostics.model_dump(mode="json"),
            "active_overrides": self._active_overrides.model_dump(mode="json"),
            "system_time": utc_now().isoformat(),
            "in_trading_window": (
                self.position_manager.is_within_strategy_a_entry_window(utc_now())
                if self.config.tunables.trend_pullback_enabled and not self.config.tunables.volatility_breakout_enabled
                else self.position_manager.is_within_entry_window()
                if self.config.tunables.volatility_breakout_enabled and not self.config.tunables.trend_pullback_enabled
                else self.position_manager.is_within_strategy_a_entry_window(utc_now()) or self.position_manager.is_within_entry_window()
            ),
        }


    async def list_decision_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        logs = await self.repo.list_decision_logs(limit=limit)
        return [l.model_dump(mode="json") for l in logs]

    async def generate_eod_report(self, session_date: Optional[str] = None) -> dict[str, Any]:
        """Build and persist the forward option-validation session report."""
        ist = timezone(timedelta(hours=5, minutes=30))
        day = session_date or utc_now().astimezone(ist).date().isoformat()
        signals = await self.repo.list_strategy_signals(limit=10000)
        signals = [s for s in signals if s["strategy"] == StrategyName.TREND_PULLBACK.value and s["timestamp"][:10] == day]
        snapshots = await self.repo.list_option_chain_snapshots(day)
        quotes = await self.repo.list_option_quotes(day)
        trades = [t for t in await self.repo.list_trades(limit=10000) if t.entry_time.astimezone(ist).date().isoformat() == day and t.strategy == StrategyName.TREND_PULLBACK]
        report: dict[str, Any] = {"session_date": day, "generated_at": utc_now().isoformat(), "directions": {}}
        for option_type in (OptionType.PUT, OptionType.CALL):
            side_trades = [t for t in trades if t.option_type == option_type]
            side_signals = [s for s in signals if s["option_type"] == option_type.value]
            side_snapshots = [s for s in snapshots if s["strategy_signal_id"] in {x["signal_id"] for x in side_signals}]
            side_quotes = [q for q in quotes if q.get("trade_id") in {t.trade_id for t in side_trades}]
            pnl = [float(t.net_pnl or 0) for t in side_trades]
            gross = sum(float(t.gross_pnl or 0) for t in side_trades)
            winners = sum(p > 0 for p in pnl)
            losers = sum(p < 0 for p in pnl)
            valid_quotes = sum(q.get("status") == "VALID" for q in side_quotes)
            report["directions"][option_type.value] = {
                "execution_state": "PAPER" if option_type == OptionType.PUT else "SHADOW_ONLY",
                "signals": len(side_signals),
                "selector_attempts": len(side_snapshots),
                "contracts_selected": sum(bool(s.get("selected_contract")) for s in side_snapshots),
                "selector_rejections": sum(not bool(s.get("selected_contract")) for s in side_snapshots),
                "paper_shadow_trades": len(side_trades),
                "option_hard_stop_exits": sum(is_option_emergency_stop(t.option_exit_reason) for t in side_trades),
                "underlying_lifecycle_exits": sum(
                    bool(t.underlying_exit_reason) and not is_option_emergency_stop(t.option_exit_reason) for t in side_trades
                ),
                "winners": winners,
                "losers": losers,
                "gross_inr": round(gross, 2),
                "net_inr": round(sum(pnl), 2),
                "average_premium_return_pct": round(sum((t.return_on_premium_pct or 0) for t in side_trades) / len(side_trades), 4) if side_trades else 0.0,
                "transaction_costs": round(sum(float(t.transaction_costs or 0) for t in side_trades), 2),
                "slippage": round(sum(float(t.slippage_cost or 0) for t in side_trades), 2),
                "missing_data_count": sum(q.get("status") != "VALID" for q in side_quotes),
                "option_data_coverage_pct": round(valid_quotes / len(side_quotes) * 100, 2) if side_quotes else 0.0,
                "trades": [t.model_dump(mode="json") for t in side_trades],
            }
            await self.repo.save_eod_report(day, option_type.value, report["directions"][option_type.value])
        return report

    async def get_eod_report(self, session_date: Optional[str] = None) -> dict[str, Any]:
        return await self.generate_eod_report(session_date)

    async def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        trades = await self.repo.list_trades(limit=limit)
        return [t.model_dump(mode="json") for t in trades]

    async def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """Runs a complete walk-forward intraday simulation against historical data."""
        return await self.simulation_engine.run_day_simulation(request)

    async def get_available_simulation_dates(
        self,
        historical_source: HistoricalReplaySource = HistoricalReplaySource.BREEZE,
    ) -> list[str]:
        """Returns replay dates available for the requested broker source."""
        return await self.simulation_engine.get_available_dates(historical_source)

    async def list_instances(self) -> list[dict[str, Any]]:
        return await self.repo.list_instances()

    async def emit_signal(
        self,
        instance_id: str,
        symbol: str,
        instrument_id: str,
        side: OrderSide,
        suggested_price: float,
        quantity: int,
        trading_mode: TradingMode = TradingMode.PAPER,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Signal:
        """Emit a calculated strategy signal and dispatch OrderIntent (backwards compatibility)."""
        signal = Signal(
            signal_id=generate_id(),
            strategy_instance_id=instance_id,
            symbol=symbol,
            side=side,
            suggested_price=suggested_price,
            suggested_quantity=quantity,
            confidence=0.92,
            metadata=metadata or {},
        )

        await self.repo.save_signal(signal)
        await self.bus.publish(
            EventEnvelope(topic=Topics.STRATEGY_SIGNAL, payload=signal.model_dump())
        )

        # Backwards-compatible signal emission may never bypass the
        # authoritative per-strategy execution policy.
        strategy_tag = str((metadata or {}).get("strategy", "")).upper()
        strategy_aliases = {
            StrategyName.TREND_PULLBACK.value: StrategyName.TREND_PULLBACK,
            "STRATEGY_A": StrategyName.TREND_PULLBACK,
            StrategyName.VOLATILITY_BREAKOUT.value: StrategyName.VOLATILITY_BREAKOUT,
            "STRATEGY_B": StrategyName.VOLATILITY_BREAKOUT,
            StrategyName.DI_CONTINUATION.value: StrategyName.DI_CONTINUATION,
            "STRATEGY_C": StrategyName.DI_CONTINUATION,
            StrategyName.SR_MOMENTUM_BREAKOUT.value: StrategyName.SR_MOMENTUM_BREAKOUT,
            "STRATEGY_D": StrategyName.SR_MOMENTUM_BREAKOUT,
        }
        policy_strategy = strategy_aliases.get(strategy_tag)
        if trading_mode == TradingMode.SHADOW:
            return signal
        if policy_strategy is not None:
            policy = self._execution_policy_for_strategy(policy_strategy)
            if trading_mode == TradingMode.LIVE and not policy.live_trading_allowed:
                await self._log_decision(
                    category="SECURITY",
                    strategy=policy_strategy.value,
                    message="Legacy signal LIVE routing blocked by execution policy",
                    details=policy.to_dict(),
                )
                return signal
            # The legacy signal endpoint is intentionally non-routing for
            # all first-class automated strategies. A/B/C/D entries are owned
            # exclusively by their validated lifecycle-aware paths above.
            if policy_strategy in {
                StrategyName.TREND_PULLBACK,
                StrategyName.VOLATILITY_BREAKOUT,
                StrategyName.DI_CONTINUATION,
                StrategyName.SR_MOMENTUM_BREAKOUT,
            }:
                return signal
        if trading_mode == TradingMode.LIVE and not self._live_orders_enabled():
            return signal

        intent = OrderIntent(
            intent_id=generate_id(),
            correlation_id=generate_id(),
            strategy_instance_id=instance_id,
            source=SourceType.STRATEGY,
            instrument_id=instrument_id,
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=suggested_price,
            product=ProductType.OPTIONS,
            trading_mode=trading_mode,
        )

        await self.oms.create_order_intent(intent)
        return signal
