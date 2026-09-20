"""Strategy Service managing auto-trading strategies, contract selection, position management, and order generation.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import asyncio
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
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from libs.config.settings import get_platform_settings
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
    SimulationRequest,
    SimulationResult,
    StrategyName,
    StrategySignal,
    ThresholdOverrides,
    TradeDirection,
    TradeLifecycleState,
    TriggerDiagnosticsResponse,
)
from services.strategy.position_manager import PositionManager, UnderlyingRiskSizer
from services.strategy.repository import StrategyRepository
from services.strategy.simulation import SimulationEngine
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy
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

        self.strategy_a = TrendPullbackStrategy(config=self.config.tunables)
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
        self._evaluation_lock = asyncio.Lock()
        self._last_eval_time: datetime = datetime.min.replace(tzinfo=timezone.utc)  # epoch → forces first-call refresh
        self._last_eod_report_date: Optional[str] = None
        self.strategy_a_telemetry = StrategyATelemetryStore()


    def _reset_setups(self, at):
        self.strategy_a.reset(at)
        self.strategy_b.reset(at)

    def _record_strategy_a_evaluation(self, features: MarketFeatures, signal: StrategySignal | None) -> None:
        futures = self._market_snapshot[2]
        if not futures:
            return
        snapshot = signal.features_snapshot if signal else {}
        candle_timestamp = snapshot.get("completed_candle_timestamp") or futures[-1].end_time.isoformat()
        contract = snapshot.get("futures_contract") or futures[-1].instrument_id
        self.strategy_a_telemetry.append(StrategyAEvaluationRecord(
            timestamp=features.timestamp.isoformat(),
            futures_contract=contract,
            completed_candle_timestamp=candle_timestamp,
            ema20=snapshot.get("ema20", features.ema20_15m), ema50=snapshot.get("ema50", features.ema50_15m),
            adx=snapshot.get("adx14", features.adx_15m), plus_di=snapshot.get("plus_di14", features.plus_di_15m),
            minus_di=snapshot.get("minus_di14", features.minus_di_15m), atr=snapshot.get("atr14", features.atr_15m),
            vwap=snapshot.get("session_vwap", features.futures_vwap), active_support=snapshot.get("support"),
            active_resistance=snapshot.get("resistance"), trend_result=snapshot.get("trend"),
            trigger=snapshot.get("trigger"), structural_stop=signal.structural_stop if signal else None,
            underlying_r=signal.r_points if signal else None, strategy_state=self.strategy_a.snapshot.state.value,
            rejection_or_invalidation_reason=self.strategy_a.last_event.reason if self.strategy_a.last_event else None,
            entry_fill=snapshot.get("entry_price"), management_event=self.strategy_a.last_event.event if self.strategy_a.last_event else None,
        ))

    def get_strategy_a_telemetry_summary(self) -> dict[str, Any]:
        return self.strategy_a_telemetry.summary()

    async def _save_runtime(self):
        await self.repo.save_runtime(self.strategy_a.export_state())
        await self.repo.save_runtime(self.strategy_b.export_state(), "volatility_breakout")

    async def initialize(self) -> None:
        await self.repo.initialize()
        self.config = await self.repo.get_auto_config()
        self._sync_subcomponents()
        self.strategy_a.restore_state(await self.repo.get_runtime())
        self.strategy_b.restore_state(await self.repo.get_runtime("volatility_breakout"))
        self._active_trades_cache = await self.repo.get_active_trades()
        await self._seed_default_strategy()

        # Start background evaluation loop if enabled
        if not self._loop_task or self._loop_task.done():
            self._is_running = True
            self._loop_task = asyncio.create_task(self._run_scheduler_loop())

    def _sync_subcomponents(self) -> None:
        self.position_manager = PositionManager(self.config.risk, self.config.session, strategy_config=self.config.tunables)
        self.risk_sizer = UnderlyingRiskSizer(self.config.risk)
        self.contract_selector = ContractSelector(self.config.option_selection)
        self.strategy_a = TrendPullbackStrategy(config=self.config.tunables)
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

    def _execution_mode_for_strategy(
        self,
        strategy: StrategyName,
        option_type: OptionType,
    ) -> AutoTradingMode:
        """Resolve a strategy's execution mode without allowing B to go live."""
        if self._is_strategy_a(strategy):
            return AutoTradingMode.SHADOW_ONLY if option_type == OptionType.CALL else AutoTradingMode.PAPER
        if strategy == StrategyName.VOLATILITY_BREAKOUT and self.config.mode == AutoTradingMode.LIVE:
            return AutoTradingMode.SHADOW_ONLY
        return self.config.mode

    def _execution_mode_for_signal(self, signal: StrategySignal) -> AutoTradingMode:
        """Resolve signal execution while keeping Strategy B non-live during validation."""
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
                await asyncio.sleep(interval)
                await self.evaluate_cycle()
                ist_now = utc_now().astimezone(timezone(timedelta(hours=5, minutes=30)))
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

        # 1. Check Kill Switch
        if self.config.kill_switch:
            self._reset_setups(now)
            await self._save_runtime()
            return {"status": "HALTED_KILL_SWITCH"}

        # 2. Gather market features
        features = await self._gather_features()
        self._last_features = features


        # 3. Manage active trades (trailing stops, thesis reversal, square-off)
        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades

        for trade in active_trades:
            await self._evaluate_active_trade(trade, features)

        self._active_trades_cache = await self.repo.get_active_trades()
        bypass = getattr(self._active_overrides, "bypass_entry_window", False)
        _, _, futures_candles = self._market_snapshot
        strategy_a_data_ready = self.config.tunables.trend_pullback_enabled and bool(futures_candles)
        if not (strategy_a_data_ready or features.data_ready or features.breakout_data_ready or (bypass and features.spot_price > 0)):
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

        # 6. Check Session Entry Window (09:30 - 14:45 IST) or override
        if not (self.position_manager.is_within_entry_window() or self._active_overrides.bypass_entry_window):
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

        # 9. Evaluate Strategy Entry Signals
        candles_5m, candles_15m, futures_candles = self._market_snapshot

        signal: Optional[StrategySignal] = None

        if self.config.tunables.trend_pullback_enabled:
            signal = self.strategy_a.evaluate(features, candles_5m, candles_15m, futures_candles=futures_candles, overrides=self._active_overrides)
            self._record_strategy_a_evaluation(features, signal)
            await self._save_runtime()

        if not signal and self.config.tunables.volatility_breakout_enabled:
            signal = self.strategy_b.evaluate(features, candles_5m, candles_15m, overrides=self._active_overrides)
            await self._save_runtime()
        elif not self.config.tunables.volatility_breakout_enabled:
            self.strategy_b.reset(now)
            await self._save_runtime()

        if not signal:
            return {"status": "NO_SIGNAL", "features": features.model_dump(mode="json")}

        if sum(t.strategy == signal.strategy for t in today_trades) >= self.config.risk.max_trades_per_strategy_per_day:
            return {"status":"STRATEGY_DAILY_TRADE_LIMIT_REACHED"}

        failures = sum(t.strategy == signal.strategy and t.exit_time is not None and (t.net_pnl or 0) < 0 for t in today_trades)
        if failures >= self.config.risk.max_failed_trades_per_strategy:
            return {"status": "STRATEGY_FAILURE_LIMIT_REACHED"}

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

        # 10. Select execution contract downstream of the underlying signal.
        execution_mode = self._execution_mode_for_signal(signal)
        chain = await self._get_option_chain()
        is_strategy_a = self._is_strategy_a(signal.strategy)
        selector_underlying = signal.spot_reference_price if is_strategy_a else features.spot_price
        selected_contract, candidates, rejection_reason = self.contract_selector.select_contract(
            direction=signal.direction,
            spot_price=selector_underlying,
            option_chain=chain,
            override_premium_cap=self._active_overrides.max_option_premium_cap,
            strategy_a=is_strategy_a,
            as_of=signal.timestamp,
        )
        if signal.strategy == StrategyName.TREND_PULLBACK and chain.get("source") not in ("BREEZE", "KITE", "LIVE"):
            selected_contract = None
            rejection_reason = chain.get("validation_rejection", "NO_REAL_OPTION_QUOTE")
        # Passive shadow capture only. The selector has already run and its
        # result is never changed by this recorder; persistence failures are
        # intentionally non-blocking for paper/live execution paths.
        if signal.strategy in (StrategyName.TREND_PULLBACK, StrategyName.VOLATILITY_BREAKOUT):
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
        if self._is_strategy_a(signal.strategy):
            sizing = self.risk_sizer.size(
                underlying_entry=signal.spot_reference_price,
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
            await self._log_decision(
                category="RISK",
                strategy=signal.strategy.value,
                message="Position sizing rejected: calculated lots < 1",
                details={"capital_cap": self.config.risk.max_trade_capital, "premium": selected_contract.ask_price},
            )
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
            entry_spot_price=signal.spot_reference_price,
            initial_structural_stop=signal.structural_stop,
            initial_r_points=signal.r_points,
            pullback_swing_low=signal.features_snapshot.get("pullback_low"),
            pullback_swing_high=signal.features_snapshot.get("pullback_high"),
            box_high=signal.features_snapshot.get("box_high"),
            box_low=signal.features_snapshot.get("box_low"),
            atr_at_lock=signal.features_snapshot.get("atr_at_lock"),
            current_option_price=selected_contract.ltp or selected_contract.ask_price,
            current_spot_price=(signal.spot_reference_price if self._is_strategy_a(signal.strategy) else features.spot_price),
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
            underlying_entry_price=signal.spot_reference_price,
            underlying_structural_stop=signal.structural_stop,
            underlying_r=signal.r_points,
            selected_option_delta=selected_contract.delta,
            selected_option_delta_source=selected_contract.greek_source,
            selected_option_gamma=selected_contract.gamma,
            selected_option_gamma_source=selected_contract.greek_source,
            risk_budget=self.config.risk.account_equity * self.config.risk.risk_per_trade_pct_of_account / 100.0,
            estimated_option_loss_at_structural_stop=(sizing.option_loss_per_lot * lots if self._is_strategy_a(signal.strategy) else None),
        )

        await self.repo.save_trade(new_trade)
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
        self._active_trades_cache.append(new_trade)

        await self._log_decision(
            category="ORDER",
            strategy=signal.strategy.value,
            message=f"ORDER ENTERED: {new_trade.mode.value} {new_trade.direction.value} {new_trade.contract_symbol} x {quantity} @ ₹{selected_contract.ask_price}",
            details=new_trade.model_dump(mode="json"),
        )

        # In LIVE mode, dispatch OrderIntent to OMS
        if execution_mode == AutoTradingMode.LIVE and not self._is_strategy_a(signal.strategy):
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

    async def _evaluate_active_trade(self, trade: ActiveTrade, features: MarketFeatures) -> None:
        """Evaluates active position stops, trailing updates, and thesis reversal score."""
        if trade.state == TradeLifecycleState.ENTRY_PENDING and not trade.entry_order_id:
            await self._log_decision("ORDER", trade.strategy.value, "Entry submission requires reconciliation; no confirmed order reference", {"trade_id": trade.trade_id})
            return
        if features.spot_price <= 0:
            features = features.model_copy(update={"spot_price": trade.current_spot_price, "closed_5m_time": None, "closed_5m_price": None})
        if trade.entry_order_id:
            order = await self.oms.get_order(trade.entry_order_id)
            if not order:
                return
            terminal = order.status.value in ("FILLED", "CANCELLED", "REJECTED", "RISK_REJECTED", "EXPIRED", "FAILED_SAFE")
            gateway = getattr(self.hist_svc, "broker_gateway", None)
            if not terminal:
                # Cancel the remainder of partial fills before managing a fixed quantity.
                timed_out = (utc_now()-trade.entry_time).total_seconds() >= self.config.risk.entry_order_timeout_sec
                if (order.filled_quantity > 0 or timed_out) and order.broker_order_id and gateway:
                    await gateway.cancel_order(order.broker_order_id, mode=order.trading_mode)
                return
            if order.filled_quantity <= 0:
                trade.state = TradeLifecycleState.CLOSED
                trade.exit_time = utc_now()
                trade.exit_reason = "ENTRY_UNFILLED_" + order.status.value
                self.strategy_a.on_exit(trade.direction, trade.exit_time)
                self.strategy_b.reset(trade.exit_time)
                await self._save_runtime()
                await self.repo.save_trade(trade)
                return
            if trade.filled_quantity != order.filled_quantity:
                trade.filled_quantity = order.filled_quantity
                trade.quantity = order.filled_quantity
                trade.entry_option_price = order.average_price
                trade.option_hard_stop_price = round(order.average_price * (1-self.config.risk.option_hard_stop_pct/100), 2)
                trade.state = TradeLifecycleState.OPEN_INITIAL_RISK
                await self.repo.save_trade(trade)

        if trade.exit_order_id:
            order = await self.oms.get_order(trade.exit_order_id)
            if not order:
                return
            if order.status.value == "FILLED":
                trade.exit_proceeds += order.average_price * order.filled_quantity
                trade.exit_filled_quantity += order.filled_quantity
                await self._close_trade(trade, features, trade.exit_proceeds/trade.quantity, trade.pending_exit_reason)
                return
            if order.status.value in ("CANCELLED", "REJECTED", "RISK_REJECTED", "EXPIRED", "FAILED_SAFE"):
                trade.exit_proceeds += order.average_price * order.filled_quantity
                trade.exit_filled_quantity += order.filled_quantity
                trade.exit_order_id = None
                await self.repo.save_trade(trade)
            else:
                bid = await self._executable_bid(trade)
                gateway = getattr(self.hist_svc, "broker_gateway", None)
                if gateway and bid and order.broker_order_id:
                    if bid != order.price:
                        await gateway.modify_order(order.broker_order_id, price=bid, mode=order.trading_mode)
                return

        quote = await self._resolve_option_quote(trade)
        self._apply_quote_to_trade(trade, quote)
        current_option_price = quote.get("ltp") or quote.get("bid")
        if quote.get("status") != "VALID" or not current_option_price or float(current_option_price) <= 0:
            # A quote gap is an incomplete validation observation, not a
            # synthetic mark and never an executable exit.
            await self.repo.save_trade(trade)
            return
        current_option_price = round(float(current_option_price), 2)
        self.position_manager.bull_derivatives_threshold = self._active_overrides.bull_derivatives_score if self._active_overrides.bull_derivatives_score is not None else 2
        self.position_manager.bear_derivatives_threshold = self._active_overrides.bear_derivatives_score if self._active_overrides.bear_derivatives_score is not None else 2
        updated_trade, exit_reason = self.position_manager.update_position(
            trade, current_option_price, features, as_of=features.timestamp
        )

        exit_reason = trade.pending_exit_reason or exit_reason
        if exit_reason:
            if trade.mode == AutoTradingMode.LIVE:
                if self._is_strategy_a(trade.strategy) and trade.selected_contract_snapshot and not self._live_orders_enabled():
                    trade.option_data_status = "LIVE_TRADING_DISABLED"
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
                    product=ProductType.OPTIONS, trading_mode=TradingMode.LIVE)
                order = await self.oms.create_order_intent(intent)
                trade.exit_order_id = order.order_id
                trade.pending_exit_reason = exit_reason
                trade.state = TradeLifecycleState.EXIT_PENDING
                await self.repo.save_trade(trade)
            else:
                sell_price = round(float(quote["bid"]) - self._paper_slippage(), 2)
                await self._close_trade(trade, features, sell_price, exit_reason, quote=quote)
        else:
            await self.repo.save_trade(updated_trade)

    async def _close_trade(self, trade, features, price, reason, quote: Optional[dict[str, Any]] = None):
        trade.state = TradeLifecycleState.CLOSED
        trade.exit_time = utc_now()
        trade.exit_option_price = price
        trade.exit_spot_price = features.spot_price if features else trade.current_spot_price
        trade.exit_reason = reason
        trade.option_exit_time = trade.exit_time
        trade.option_exit_reason = reason
        if str(reason) == "OPTION_HARD_STOP_HIT":
            trade.underlying_exit_reason = "OPTION_HARD_STOP_PREEMPTED_UNDERLYING"
            trade.underlying_exit_time = None
        else:
            trade.underlying_exit_reason = reason
            trade.underlying_exit_time = trade.exit_time

        raw_entry = float(trade.entry_raw_ask or trade.entry_option_price)
        raw_exit = float((quote or {}).get("bid") or price)
        trade.raw_gross_option_pnl = round((raw_exit - raw_entry) * trade.quantity, 2)
        trade.gross_pnl = round((price - trade.entry_option_price) * trade.quantity, 2)
        entry_slip = abs(float(trade.entry_executable_price or trade.entry_option_price) - raw_entry)
        exit_slip = abs(raw_exit - float(price))
        trade.slippage_cost = round((entry_slip + exit_slip) * trade.quantity, 2)
        turnover = (float(trade.entry_option_price) + float(price)) * trade.quantity
        buy_turnover = float(trade.entry_option_price) * trade.quantity
        sell_turnover = float(price) * trade.quantity
        r = self.config.risk
        trade.brokerage = round(2 * r.paper_brokerage_per_order, 2)
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
        trade.realized_r = trade.current_r
        if trade.gross_pnl < 0:
            self._last_loss_exit_time = trade.exit_time
        self.strategy_a.on_exit(trade.direction, trade.exit_time)
        self.strategy_b.reset(trade.exit_time)
        self._active_trades_cache = [t for t in self._active_trades_cache if t.trade_id != trade.trade_id]
        await self._save_runtime()
        await self.repo.save_trade(trade)
        await self._record_execution({
            "trade_id": trade.trade_id,
            "side": "SELL",
            "timestamp": trade.exit_time.isoformat(),
            "raw_bid": (quote or {}).get("bid"),
            "raw_ask": (quote or {}).get("ask"),
            "raw_ltp": (quote or {}).get("ltp"),
            "executable_price": price,
            "slippage_points": self._paper_slippage(),
            "quantity": trade.quantity,
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
            )
            order = await self.oms.create_order_intent(intent)
            target.exit_order_id = order.order_id
            target.pending_exit_reason = reason
            target.state = TradeLifecycleState.EXIT_PENDING
            await self.repo.save_trade(target)
            return target

    # --- Market Data & Chain Fetching ---
    async def _gather_features(self) -> MarketFeatures:
        candles_5m = await self._get_recent_candles("5m")
        candles_15m = await self._get_recent_candles("15m")
        futures = []
        inst_svc = getattr(self.chain_svc, "inst_svc", None)
        if inst_svc:
            instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
            today = utc_now().astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
            eligible = sorted((i for i in instruments if i.segment == "FUTURES" and i.tradable
                               and i.expiry and i.expiry >= today), key=lambda i: i.expiry)
            if eligible:
                futures = await self._get_recent_candles("5m", eligible[0].instrument_id)
            elif getattr(getattr(self.chain_svc, "broker_gateway", None), "active_broker_name", None) == "kite":
                # The local instrument seed contains spot/options only. Kite's
                # adapter resolves this virtual ID to the nearest NIFTY future
                # from the live NFO instrument master.
                futures = await self._get_recent_candles("5m", "INST-NIFTY-FUT-NEAREST")
        self._market_snapshot = (candles_5m, candles_15m, futures)
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
            candles = await self.hist_svc.get_candles(instrument_id=instrument_id, interval=interval)
            now = utc_now()
            expected_sec = 900 if "15" in interval else 300
            return sorted({c.start_time: c for c in candles
                           if c.source in ("BREEZE", "KITE", "LIVE") and c.end_time <= now
                           and c.interval == interval
                           and abs((c.end_time - c.start_time).total_seconds() - expected_sec) <= 5
                           }.values(), key=lambda c: c.start_time)
        except Exception:
            logger.exception("Real candle retrieval failed")
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
                    return chain
                # Offline/synthetic matrices remain usable by the UI, but may
                # never create a forward option-validation trade.
                if chain.get("strikes"):
                    return {**chain, "source": "UNAVAILABLE", "validation_rejection": "synthetic option prices are not executable"}
            except Exception:
                logger.exception("Option-chain retrieval failed")
        return {"source": "UNAVAILABLE", "strikes": []}

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

    async def get_trigger_diagnostics(self) -> TriggerDiagnosticsResponse:
        """Gathers granular condition diagnostics across all strategies and session gates."""
        features = await self._gather_features()
        candles_5m, candles_15m, futures_candles = self._market_snapshot

        diag_a = self.strategy_a.diagnose(features, candles_5m, candles_15m, overrides=self._active_overrides, futures_candles=futures_candles)
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

        spot = features.spot_price if (features and features.spot_price > 0) else 0.0
        if spot <= 0 and self.mkt_svc:
            q = self.mkt_svc.get_latest_quote("INST-NIFTY-INDEX")
            if q and q.last_price > 0:
                spot = q.last_price
        if spot <= 0:
            candles_5m = await self._get_recent_candles("5m")
            if candles_5m:
                spot = candles_5m[-1].close

        if spot <= 0:
            return {
                "status": "DATA_UNAVAILABLE",
                "reason": "Missing real spot price: No current or historical NIFTY spot price available to price options and calculate risk",
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

        if strategy == StrategyName.TREND_PULLBACK:
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
        if execution_mode == AutoTradingMode.LIVE and strategy != StrategyName.TREND_PULLBACK:
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
        # Fix 3: Ensure strategy diagnostics are fresh (trigger eval if >5s stale)
        secs_since_eval = (utc_now() - self._last_eval_time).total_seconds()
        if secs_since_eval >= 5:
            try:
                await asyncio.wait_for(self.evaluate_cycle(), timeout=3.0)
            except Exception:
                pass  # never fail the status call due to evaluation errors

        active_trades = await self.repo.get_active_trades()
        features = self._last_features or await self._gather_features()

        # Dynamically refresh active trades so UI reflects live market movement every 1.5s
        for trade in active_trades:
            if trade.state in (
                TradeLifecycleState.OPEN_INITIAL_RISK,
                TradeLifecycleState.PROTECTED_BREAKEVEN,
                TradeLifecycleState.PROFIT_LOCKED,
                TradeLifecycleState.RUNNER_MODE,
            ):
                # Fix 2a: patch stale spot so stops don't fire on spot=0 outside market hours
                safe_features = features
                if features.spot_price <= 0:
                    safe_features = features.model_copy(update={
                        "spot_price": trade.current_spot_price or trade.entry_spot_price,
                        "closed_5m_time": None,
                        "closed_5m_price": None,
                    })
                quote = await self._resolve_option_quote(trade)
                self._apply_quote_to_trade(trade, quote)
                cur_price = quote.get("ltp") or quote.get("bid")
                if quote.get("status") == "VALID" and cur_price and float(cur_price) > 0:
                    updated_trade, _ = self.position_manager.update_position(trade, round(float(cur_price), 2), safe_features, as_of=safe_features.timestamp)
                    await self.repo.save_trade(updated_trade)
                else:
                    # Preserve the data-quality gap and do not let a stale or
                    # missing quote manufacture a lifecycle mark/fill.
                    await self.repo.save_trade(trade)

        active_trades = await self.repo.get_active_trades()
        self._active_trades_cache = active_trades
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
                "option_hard_stop_exits": sum(t.option_exit_reason == "OPTION_HARD_STOP_HIT" for t in side_trades),
                "underlying_lifecycle_exits": sum(
                    bool(t.underlying_exit_reason) and t.option_exit_reason != "OPTION_HARD_STOP_HIT" for t in side_trades
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

    async def get_available_simulation_dates(self) -> list[str]:
        """Returns dates available for historical simulation."""
        return await self.simulation_engine.get_available_dates()

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

        # Strategy A forward-validation signals are audit/simulation only.
        # This guard is intentionally before construction of any OMS intent.
        strategy_tag = str((metadata or {}).get("strategy", "")).upper()
        is_strategy_a_validation = strategy_tag in {StrategyName.TREND_PULLBACK.value, "STRATEGY_A", "CALL", "PUT"}
        is_strategy_b_validation = strategy_tag == StrategyName.VOLATILITY_BREAKOUT.value
        if trading_mode == TradingMode.SHADOW or is_strategy_a_validation or is_strategy_b_validation:
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
