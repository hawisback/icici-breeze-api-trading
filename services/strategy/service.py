"""Strategy Service managing auto-trading strategies, contract selection, position management, and order generation.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.oms.service import OMSService
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingConfig,
    AutoTradingMode,
    DecisionLogEntry,
    GateBlockers,
    MarketFeatures,
    OptionType,
    StrategyName,
    StrategySignal,
    ThresholdOverrides,
    TradeDirection,
    TradeLifecycleState,
    TriggerDiagnosticsResponse,
)
from services.strategy.position_manager import PositionManager
from services.strategy.repository import StrategyRepository
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy

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
        self.position_manager = PositionManager(self.config.risk, self.config.session)
        self.contract_selector = ContractSelector(self.config.option_selection)

        self.strategy_a = TrendPullbackStrategy(
            adx_threshold=self.config.tunables.adx_threshold,
            rvol_threshold=self.config.tunables.rvol_threshold,
        )
        self.strategy_b = VolatilityBreakoutStrategy(
            rvol_threshold=self.config.tunables.rvol_threshold,
            adx_threshold=self.config.tunables.adx_threshold,
        )

        self._loop_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_features: Optional[MarketFeatures] = None
        self._active_trades_cache: list[ActiveTrade] = []
        self._last_loss_exit_time: Optional[datetime] = None
        self._active_overrides: ThresholdOverrides = ThresholdOverrides()

    async def initialize(self) -> None:
        await self.repo.initialize()
        self.config = await self.repo.get_auto_config()
        self._sync_subcomponents()
        self._active_trades_cache = await self.repo.get_active_trades()
        await self._seed_default_strategy()

        # Start background evaluation loop if enabled
        if not self._loop_task or self._loop_task.done():
            self._is_running = True
            self._loop_task = asyncio.create_task(self._run_scheduler_loop())

    def _sync_subcomponents(self) -> None:
        self.position_manager = PositionManager(self.config.risk, self.config.session)
        self.contract_selector = ContractSelector(self.config.option_selection)
        self.strategy_a = TrendPullbackStrategy(
            adx_threshold=self.config.tunables.adx_threshold,
            rvol_threshold=self.config.tunables.rvol_threshold,
        )
        self.strategy_b = VolatilityBreakoutStrategy(
            rvol_threshold=self.config.tunables.rvol_threshold,
            adx_threshold=self.config.tunables.adx_threshold,
        )

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
            mode=TradingMode.PAPER if self.config.mode == AutoTradingMode.PAPER else TradingMode.LIVE,
            symbol="NIFTY",
            parameters=self.config.model_dump(),
            status="RUNNING",
        )

    # --- Configuration Management ---
    async def get_config(self) -> AutoTradingConfig:
        return self.config

    async def update_config(self, new_config: AutoTradingConfig) -> AutoTradingConfig:
        # If active positions exist, do not allow mode change between PAPER and LIVE
        if self._active_trades_cache and new_config.mode != self.config.mode:
            raise ValueError("Cannot switch trading mode while positions are active.")

        self.config = new_config
        self._sync_subcomponents()
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
                await asyncio.sleep(interval)
                await self.evaluate_cycle()
                # Broadcast live status update to UI over event bus
                st = await self.get_status()
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.STRATEGY_SIGNAL,
                        payload={"event": "STATUS_UPDATE", "data": st},
                    )
                )
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
        """Executes a single evaluation cycle: features -> active trade management -> entry signals."""
        now = utc_now()

        # 1. Check Kill Switch
        if self.config.kill_switch:
            return {"status": "HALTED_KILL_SWITCH"}

        # 2. Gather market features
        features = await self._gather_features()
        self._last_features = features

        # 3. Manage active trades (trailing stops, thesis reversal, square-off)
        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades

        for trade in active_trades:
            await self._evaluate_active_trade(trade, features)

        # 4. If active positions reached limit, do not seek new entries
        if len(self._active_trades_cache) >= self.config.risk.max_concurrent_positions:
            return {"status": "MAX_CONCURRENT_POSITIONS_REACHED", "active_count": len(self._active_trades_cache)}

        # 5. Check if Auto Trade is enabled
        if not self.config.auto_trade_enabled:
            return {"status": "AUTO_TRADE_DISABLED"}

        # 6. Check Session Entry Window (09:30 - 14:45 IST) or override
        if not (self.position_manager.is_within_entry_window() or self._active_overrides.bypass_entry_window):
            return {"status": "OUTSIDE_ENTRY_WINDOW"}

        # 7. Check Cooldown after loss
        if self._last_loss_exit_time:
            mins_since_loss = (now - self._last_loss_exit_time).total_seconds() / 60.0
            if mins_since_loss < self.config.risk.cooldown_after_loss_min:
                return {
                    "status": "IN_LOSS_COOLDOWN",
                    "cooldown_remaining_min": round(self.config.risk.cooldown_after_loss_min - mins_since_loss, 1),
                }

        # 8. Check Daily Trade Count Limit
        today_trades = await self.repo.list_trades(limit=50)
        today_str = now.strftime("%Y-%m-%d")
        today_count = sum(1 for t in today_trades if t.entry_time.strftime("%Y-%m-%d") == today_str)
        if today_count >= self.config.risk.max_trades_per_day:
            return {"status": "DAILY_TRADE_LIMIT_REACHED", "today_trades": today_count}

        # 9. Evaluate Strategy Entry Signals
        candles_5m = await self._get_recent_candles("5m")
        candles_15m = await self._get_recent_candles("15m")

        signal: Optional[StrategySignal] = None

        if self.config.tunables.trend_pullback_enabled:
            signal = self.strategy_a.evaluate(features, candles_5m, candles_15m, overrides=self._active_overrides)

        if not signal and self.config.tunables.volatility_breakout_enabled:
            signal = self.strategy_b.evaluate(features, candles_5m, candles_15m, overrides=self._active_overrides)

        if not signal:
            return {"status": "NO_SIGNAL", "features": features.model_dump(mode="json")}

        # Signal detected!
        await self.repo.save_strategy_signal(signal)
        await self._log_decision(
            category="SETUP",
            strategy=signal.strategy.value,
            message=f"Setup Triggered: {signal.strategy.value} {signal.direction.value} ({signal.option_type.value})",
            details={
                "spot": signal.spot_reference_price,
                "stop": signal.structural_stop,
                "r_points": signal.r_points,
                "derivatives_score": signal.derivatives_score,
            },
        )

        # 10. Contract Selection under Max Option Premium Cap
        chain = await self._get_option_chain()
        selected_contract, candidates, rejection_reason = self.contract_selector.select_contract(
            direction=signal.direction,
            spot_price=features.spot_price,
            option_chain=chain,
            override_premium_cap=self._active_overrides.max_option_premium_cap,
        )

        if not selected_contract:
            await self._log_decision(
                category="CONTRACT_SELECTION",
                strategy=signal.strategy.value,
                message=f"Contract selection failed: {rejection_reason}",
                details={"candidates_checked": len(candidates), "cap": self.config.option_selection.max_option_premium},
            )
            return {"status": "CONTRACT_SELECTION_FAILED", "reason": rejection_reason}

        await self._log_decision(
            category="CONTRACT_SELECTION",
            strategy=signal.strategy.value,
            message=f"Selected {selected_contract.symbol} @ ₹{selected_contract.ask_price} (Cap: ₹{self.config.option_selection.max_option_premium})",
            details=selected_contract.model_dump(mode="json"),
        )

        # 11. Position Sizing
        lots, quantity = self.position_manager.calculate_position_size(
            entry_premium=selected_contract.ask_price,
            account_equity=500000.0,
            lot_size=selected_contract.lot_size,
        )

        if lots < 1:
            await self._log_decision(
                category="RISK",
                strategy=signal.strategy.value,
                message="Position sizing rejected: calculated lots < 1",
                details={"capital_cap": self.config.risk.max_trade_capital, "premium": selected_contract.ask_price},
            )
            return {"status": "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"}

        # 12. Check LIVE Arming Gate
        if self.config.mode == AutoTradingMode.LIVE and not self.config.system_armed:
            await self._log_decision(
                category="SECURITY",
                strategy=signal.strategy.value,
                message="LIVE order execution blocked: System is not ARMED",
                details={"symbol": selected_contract.symbol, "lots": lots, "quantity": quantity},
            )
            return {"status": "LIVE_SYSTEM_NOT_ARMED"}

        # 13. Create Active Trade and dispatch Order
        trade_id = f"TRD-{int(now.timestamp())}"
        hard_stop_price = round(
            selected_contract.ask_price * (1.0 - (self.config.risk.option_hard_stop_pct / 100.0)),
            2,
        )

        new_trade = ActiveTrade(
            trade_id=trade_id,
            mode=self.config.mode,
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
            entry_option_price=selected_contract.ask_price,
            entry_spot_price=features.spot_price,
            initial_structural_stop=signal.structural_stop,
            initial_r_points=signal.r_points,
            current_option_price=selected_contract.ask_price,
            current_spot_price=features.spot_price,
            current_trailing_stop=signal.structural_stop,
            option_hard_stop_price=hard_stop_price,
            current_r=0.0,
            peak_r=0.0,
            state=TradeLifecycleState.OPEN_INITIAL_RISK,
        )

        await self.repo.save_trade(new_trade)
        self._active_trades_cache.append(new_trade)

        await self._log_decision(
            category="ORDER",
            strategy=signal.strategy.value,
            message=f"ORDER ENTERED: {new_trade.mode.value} {new_trade.direction.value} {new_trade.contract_symbol} x {quantity} @ ₹{selected_contract.ask_price}",
            details=new_trade.model_dump(mode="json"),
        )

        # In LIVE mode, dispatch OrderIntent to OMS
        if self.config.mode == AutoTradingMode.LIVE:
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
            await self.oms.create_order_intent(intent)

        # Broadcast update
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.STRATEGY_SIGNAL,
                payload={"event": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")},
            )
        )

        return {"status": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")}

    async def _evaluate_active_trade(self, trade: ActiveTrade, features: MarketFeatures) -> None:
        """Evaluates active position stops, trailing updates, and thesis reversal score."""
        # Estimate current option price
        # In a full options feed, fetch live quote. If mock/simulated, calculate estimated delta move:
        current_option_price = trade.current_option_price
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote(trade.contract_instrument_id)
            if q and q.last_price > 0:
                current_option_price = q.last_price
            else:
                # Delta approximation: Delta ~ 0.35
                spot_delta = features.spot_price - trade.entry_spot_price
                if trade.direction == TradeDirection.BEARISH:
                    spot_delta = -spot_delta
                current_option_price = max(1.0, round(trade.entry_option_price + (spot_delta * 0.35), 2))

        updated_trade, exit_reason = self.position_manager.update_position(
            trade, current_option_price, features
        )

        if exit_reason:
            # Position exited!
            updated_trade.state = TradeLifecycleState.CLOSED
            updated_trade.exit_time = utc_now()
            updated_trade.exit_option_price = current_option_price
            updated_trade.exit_spot_price = features.spot_price
            updated_trade.exit_reason = exit_reason

            gross_pnl = round((current_option_price - updated_trade.entry_option_price) * updated_trade.quantity, 2)
            updated_trade.gross_pnl = gross_pnl
            updated_trade.net_pnl = round(gross_pnl - 40.0, 2)  # brokerage/tax deduction
            updated_trade.realized_r = updated_trade.current_r

            if gross_pnl < 0:
                self._last_loss_exit_time = utc_now()

            await self.repo.save_trade(updated_trade)

            await self._log_decision(
                category="EXIT",
                strategy=updated_trade.strategy.value,
                message=f"POSITION CLOSED: {updated_trade.contract_symbol} | Reason: {exit_reason} | PnL: ₹{gross_pnl} ({updated_trade.realized_r}R)",
                details=updated_trade.model_dump(mode="json"),
            )

            # In LIVE mode, dispatch square-off intent
            if updated_trade.mode == AutoTradingMode.LIVE:
                exit_intent = OrderIntent(
                    intent_id=generate_id(),
                    correlation_id=updated_trade.trade_id,
                    strategy_instance_id="INST-NIFTY-AUTO-ENGINE",
                    source=SourceType.STRATEGY,
                    instrument_id=updated_trade.contract_instrument_id,
                    symbol=updated_trade.contract_symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    quantity=updated_trade.quantity,
                    price=current_option_price,
                    product=ProductType.OPTIONS,
                    trading_mode=TradingMode.LIVE,
                )
                await self.oms.create_order_intent(exit_intent)

            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.STRATEGY_SIGNAL,
                    payload={"event": "TRADE_CLOSED", "trade": updated_trade.model_dump(mode="json")},
                )
            )

        else:
            # Update active trade state
            await self.repo.save_trade(updated_trade)

    async def manual_exit_trade(self, trade_id: str, reason: str = "MANUAL_UI_EXIT") -> Optional[ActiveTrade]:
        """Manually exit an active trade immediately."""
        active_trades = await self.repo.get_active_trades()
        target = next((t for t in active_trades if t.trade_id == trade_id), None)
        if not target:
            return None

        features = self._last_features or await self._gather_features()
        target.state = TradeLifecycleState.CLOSED
        target.exit_time = utc_now()
        target.exit_option_price = target.current_option_price
        target.exit_spot_price = features.spot_price
        target.exit_reason = reason
        gross = round((target.current_option_price - target.entry_option_price) * target.quantity, 2)
        target.gross_pnl = gross
        target.net_pnl = round(gross - 40.0, 2)
        target.realized_r = target.current_r

        await self.repo.save_trade(target)
        await self._log_decision(
            category="EXIT",
            strategy=target.strategy.value,
            message=f"MANUAL EXIT: {target.contract_symbol} | Reason: {reason} | PnL: ₹{gross}",
            details=target.model_dump(mode="json"),
        )
        return target

    # --- Market Data & Chain Fetching ---
    async def _gather_features(self) -> MarketFeatures:
        candles_5m = await self._get_recent_candles("5m")
        candles_15m = await self._get_recent_candles("15m")
        chain = await self._get_option_chain()

        spot = 23217.60
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote("INST-NIFTY-INDEX")
            if q and q.last_price > 0:
                spot = q.last_price
        elif candles_5m:
            spot = candles_5m[-1].close

        return FeatureEngine.compute_all_features(
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            option_chain=chain,
            spot_price=spot,
        )

    async def _get_recent_candles(self, interval: str) -> list[Candle]:
        if self.hist_svc:
            try:
                candles = await self.hist_svc.get_candles(
                    instrument_id="INST-NIFTY-INDEX",
                    interval=interval,
                )
                if candles and len(candles) >= 5:
                    return candles
            except Exception:
                pass

        # Robust synthetic fallback for testing / pre-market
        spot = 23220.0
        candles: list[Candle] = []
        now = utc_now()
        for i in range(30, 0, -1):
            ts = datetime.fromtimestamp(now.timestamp() - (i * 300), tz=timezone.utc)
            c = Candle(
                instrument_id="INST-NIFTY-INDEX",
                interval=interval,
                start_time=ts,
                end_time=datetime.fromtimestamp(ts.timestamp() + 300, tz=timezone.utc),
                open=spot - 5.0 + (i * 0.5),
                high=spot + 15.0 + (i * 0.5),
                low=spot - 10.0 + (i * 0.5),
                close=spot + (i * 0.5),
                volume=150000,
            )
            candles.append(c)
        return candles

    async def _get_option_chain(self) -> dict[str, Any]:
        if self.chain_svc:
            try:
                return await self.chain_svc.get_chain(underlying="NIFTY")
            except Exception:
                pass

        # Fallback realistic NIFTY chain
        spot = 23217.60
        atm = 23200
        strikes = []
        for strike in range(atm - 300, atm + 350, 50):
            dist = strike - spot
            call_price = max(5.0, round(max(0.0, -dist) + 75.0 - (abs(dist) * 0.25), 1))
            put_price = max(5.0, round(max(0.0, dist) + 75.0 - (abs(dist) * 0.25), 1))
            strikes.append(
                {
                    "strike": strike,
                    "call": {
                        "instrument_id": f"NIFTY-2026-09-22-{strike}-CE",
                        "ltp": call_price,
                        "ask": call_price + 0.5,
                        "bid": call_price - 0.5,
                        "open_interest": 54000,
                        "volume": 12000,
                    },
                    "put": {
                        "instrument_id": f"NIFTY-2026-09-22-{strike}-PE",
                        "ltp": put_price,
                        "ask": put_price + 0.5,
                        "bid": put_price - 0.5,
                        "open_interest": 48000,
                        "volume": 9800,
                    },
                }
            )
        return {"underlying": "NIFTY", "atm_strike": atm, "strikes": strikes}

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
        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message="Manual threshold overrides updated",
            details=overrides.model_dump(mode="json"),
        )
        return self._active_overrides

    async def reset_overrides(self) -> ThresholdOverrides:
        self._active_overrides = ThresholdOverrides()
        await self._log_decision(
            category="CONFIG",
            strategy="SYSTEM",
            message="Manual threshold overrides reset to strategy defaults",
            details=self._active_overrides.model_dump(mode="json"),
        )
        return self._active_overrides

    async def get_trigger_diagnostics(self) -> TriggerDiagnosticsResponse:
        """Gathers granular condition diagnostics across all strategies and session gates."""
        features = self._last_features or await self._gather_features()
        candles_5m = await self._get_recent_candles("5m")
        candles_15m = await self._get_recent_candles("15m")

        diag_a = self.strategy_a.diagnose(features, candles_5m, candles_15m, overrides=self._active_overrides)
        diag_b = self.strategy_b.diagnose(features, candles_5m, overrides=self._active_overrides)

        now = utc_now()
        is_window = self.position_manager.is_within_entry_window()
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

        primary = "All system gates clear — monitoring live market candles for technical trigger"
        if self.config.kill_switch:
            primary = "Emergency Kill Switch is ACTIVE"
        elif not self.config.auto_trade_enabled:
            primary = "Auto-Trading Execution is DISABLED"
        elif not effective_window:
            primary = "Outside intraday entry window (09:30 - 14:45 IST). Set 'Bypass Entry Window' in Overrides to test now."
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
            within_trading_window=effective_window,
            max_positions_reached=pos_blocked,
            in_cooldown=in_cooldown,
            daily_trades_count=today_count,
            daily_trades_max=max_daily,
            system_armed=self.config.system_armed,
            primary_blocker=primary,
        )

        return TriggerDiagnosticsResponse(
            system_time=now,
            gates=gates,
            strategies=[*diag_a, *diag_b],
            active_overrides=self._active_overrides,
        )

    async def force_entry(
        self,
        strategy: StrategyName = StrategyName.TREND_PULLBACK,
        direction: TradeDirection = TradeDirection.BULLISH,
        option_type: Optional[OptionType] = None,
        override_premium_cap: Optional[float] = None,
    ) -> dict[str, Any]:
        """Manually force a strategy trade setup entry immediately.
        
        Performs automated contract selection, position sizing, risk guardrail enforcement,
        and registers the trade with hard stop (-25%) and trailing ladder targets (+1R, +1.5R, +2R).
        """
        now = utc_now()
        features = self._last_features or await self._gather_features()
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)

        if option_type is None:
            option_type = OptionType.CALL if direction == TradeDirection.BULLISH else OptionType.PUT

        # 1. Structural stop and R points
        if direction == TradeDirection.BULLISH:
            structural_stop = round(spot - (0.85 * atr), 2)
            r_points = round(spot - structural_stop, 2)
        else:
            structural_stop = round(spot + (0.85 * atr), 2)
            r_points = round(structural_stop - spot, 2)

        # 2. Contract Selection under Max Premium Cap
        chain = await self._get_option_chain()
        cap = override_premium_cap or self._active_overrides.max_option_premium_cap
        selected_contract, candidates, rejection_reason = self.contract_selector.select_contract(
            direction=direction,
            spot_price=spot,
            option_chain=chain,
            override_premium_cap=cap,
        )

        if not selected_contract:
            await self._log_decision(
                category="FORCE_ENTRY",
                strategy=strategy.value,
                message=f"Forced entry failed: {rejection_reason}",
                details={"candidates_checked": len(candidates), "cap": cap or self.config.option_selection.max_option_premium},
            )
            return {"status": "CONTRACT_SELECTION_FAILED", "reason": rejection_reason, "candidates": candidates}

        # 3. Position Sizing
        lots, quantity = self.position_manager.calculate_position_size(
            entry_premium=selected_contract.ask_price,
            account_equity=500000.0,
            lot_size=selected_contract.lot_size,
        )

        if lots < 1:
            lots = 1
            quantity = selected_contract.lot_size

        # 4. Check LIVE Arming Gate
        if self.config.mode == AutoTradingMode.LIVE and not self.config.system_armed:
            await self._log_decision(
                category="SECURITY",
                strategy=strategy.value,
                message="Forced LIVE order execution blocked: System is not ARMED",
                details={"symbol": selected_contract.symbol, "lots": lots, "quantity": quantity},
            )
            return {"status": "LIVE_SYSTEM_NOT_ARMED", "message": "System is in LIVE mode but not armed"}

        # 5. Create Active Trade
        trade_id = f"TRD-FORCED-{int(now.timestamp())}"
        hard_stop_price = round(
            selected_contract.ask_price * (1.0 - (self.config.risk.option_hard_stop_pct / 100.0)),
            2,
        )

        new_trade = ActiveTrade(
            trade_id=trade_id,
            mode=self.config.mode,
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
            entry_option_price=selected_contract.ask_price,
            entry_spot_price=spot,
            initial_structural_stop=structural_stop,
            initial_r_points=r_points,
            current_option_price=selected_contract.ask_price,
            current_spot_price=spot,
            current_trailing_stop=structural_stop,
            option_hard_stop_price=hard_stop_price,
            current_r=0.0,
            peak_r=0.0,
            state=TradeLifecycleState.OPEN_INITIAL_RISK,
        )

        await self.repo.save_trade(new_trade)
        self._active_trades_cache.append(new_trade)

        await self._log_decision(
            category="ORDER",
            strategy=strategy.value,
            message=f"FORCED ENTRY TRIGGERED: {new_trade.mode.value} {new_trade.direction.value} {new_trade.contract_symbol} x {quantity} @ ₹{selected_contract.ask_price}",
            details=new_trade.model_dump(mode="json"),
        )

        # In LIVE mode, dispatch OrderIntent
        if self.config.mode == AutoTradingMode.LIVE:
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
            await self.oms.create_order_intent(intent)

        await self.bus.publish(
            EventEnvelope(
                topic=Topics.STRATEGY_SIGNAL,
                payload={"event": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")},
            )
        )

        return {"status": "TRADE_OPENED", "trade": new_trade.model_dump(mode="json")}

    async def get_status(self) -> dict[str, Any]:
        """Status payload consumed by the Auto-Trading UI."""
        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades
        features = self._last_features or await self._gather_features()
        latest_signals = await self.repo.list_strategy_signals(limit=10)
        diagnostics = await self.get_trigger_diagnostics()

        return {
            "config": self.config.model_dump(mode="json"),
            "features": features.model_dump(mode="json"),
            "active_trades": [t.model_dump(mode="json") for t in active_trades],
            "signals": latest_signals,
            "strategies": {
                "trend_pullback": {
                    "enabled": self.config.tunables.trend_pullback_enabled,
                    "state": "TRIGGERED" if any(t.strategy == StrategyName.TREND_PULLBACK for t in active_trades) else "SEARCHING",
                },
                "volatility_breakout": {
                    "enabled": self.config.tunables.volatility_breakout_enabled,
                    "state": "TRIGGERED" if any(t.strategy == StrategyName.VOLATILITY_BREAKOUT for t in active_trades) else "SEARCHING",
                },
            },
            "trigger_diagnostics": diagnostics.model_dump(mode="json"),
            "active_overrides": self._active_overrides.model_dump(mode="json"),
            "system_time": utc_now().isoformat(),
            "in_trading_window": self.position_manager.is_within_entry_window(),
        }

    async def list_decision_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        logs = await self.repo.list_decision_logs(limit=limit)
        return [l.model_dump(mode="json") for l in logs]

    async def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        trades = await self.repo.list_trades(limit=limit)
        return [t.model_dump(mode="json") for t in trades]

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

        if trading_mode == TradingMode.SHADOW:
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
