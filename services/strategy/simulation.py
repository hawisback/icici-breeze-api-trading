"""Simulation & Day-Replay Engine for NIFTY Intraday Options Auto-Trading.
Enables full-session backtesting and walk-forward replay against historical candles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Optional

from libs.contracts.models import Candle, utc_now
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    DecisionLogEntry,
    MarketFeatures,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    SimulatedTradeRecord,
    SimulationBarSnapshot,
    SimulationRequest,
    SimulationResult,
    StrategyName,
    ThresholdOverrides,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.position_manager import PositionManager
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


class SimulationEngine:
    """Replays historical 5m candles bar-by-bar to simulate intraday trading."""

    def __init__(
        self,
        historical_service: Optional[Any] = None,
        risk_config: Optional[RiskConfig] = None,
        session_config: Optional[SessionTimersConfig] = None,
        tunables=None,
    ) -> None:
        self.hist_svc = historical_service
        self.risk_config = risk_config or RiskConfig()
        self.session_config = session_config or SessionTimersConfig()
        from services.strategy.models import StrategyTunablesConfig
        self.tunables = tunables or StrategyTunablesConfig()

    @staticmethod
    def resample_to_15m(candles_5m: list[Candle], instrument_id: str = "INST-NIFTY-INDEX") -> list[Candle]:
        """Resample a 5m candle sequence into 15m candles."""
        if not candles_5m:
            return []
        buckets: dict[datetime, list[Candle]] = {}
        for c in candles_5m:
            dt_ist = c.start_time.astimezone(IST)
            bucket_minute = (dt_ist.minute // 15) * 15
            bucket_ist = dt_ist.replace(minute=bucket_minute, second=0, microsecond=0)
            bucket_utc = bucket_ist.astimezone(timezone.utc)
            buckets.setdefault(bucket_utc, []).append(c)

        res: list[Candle] = []
        for b_start, b_candles in sorted(buckets.items()):
            b_candles.sort(key=lambda c: c.start_time)
            if len(b_candles) != 3 or any(c.start_time != b_start + timedelta(minutes=5*i) for i, c in enumerate(b_candles)):
                continue
            b_end = b_start + timedelta(minutes=15)
            res.append(
                Candle(
                    instrument_id=instrument_id,
                    interval="15m",
                    start_time=b_start,
                    end_time=b_end,
                    open=b_candles[0].open,
                    high=max(x.high for x in b_candles),
                    low=min(x.low for x in b_candles),
                    close=b_candles[-1].close,
                    volume=sum(x.volume for x in b_candles),
                    open_interest=b_candles[-1].open_interest,
                    source=b_candles[0].source,
                )
            )
        return res

    async def get_available_dates(self) -> list[str]:
        """Discovers distinct trading session dates available in historical storage."""
        dates: set[str] = set()
        if self.hist_svc and hasattr(self.hist_svc, "repo"):
            try:
                async with self.hist_svc.repo.engine.connect() as conn:
                    cursor = await conn.execute("""
                        SELECT DISTINCT substr(start_time, 1, 10) as day
                        FROM historical_candles
                        WHERE instrument_id = 'INST-NIFTY-INDEX' AND interval = '5m'
                        AND source IN ('BREEZE', 'LIVE')
                        ORDER BY day DESC
                        LIMIT 30;
                    """)
                    rows = await cursor.fetchall()
                    for r in rows:
                        if r[0]:
                            dates.add(str(r[0]))
            except Exception as ex:
                logger.warning("Failed to query historical dates: %s", ex)

        return sorted(list(dates), reverse=True)

    async def _fetch_session_candles(self, date_str: str, instrument_id: str) -> tuple[list[Candle], list[Candle]]:
        """Retrieves warm-up candles and session candles for the given date.
        
        Returns:
            (warmup_candles, session_candles)
        """
        # Parse date in IST
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        session_start_ist = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST)
        session_end_ist = datetime(target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST)

        session_start_utc = session_start_ist.astimezone(timezone.utc)
        session_end_utc = session_end_ist.astimezone(timezone.utc)
        warmup_start_utc = session_start_utc - timedelta(days=7)  # enough real 15m bars to initialize EMA50

        all_candles: list[Candle] = []

        # 1. Try fetching from HistoricalService / Breeze
        if self.hist_svc:
            try:
                # If breeze is available, proactively fetch to refresh cache
                if getattr(self.hist_svc, "broker_gateway", None):
                    await self.hist_svc.fetch_candles_from_breeze(instrument_id, interval="5m", days_back=7)

                candles = await self.hist_svc.get_candles(
                    instrument_id=instrument_id,
                    interval="5m",
                    start_time=warmup_start_utc,
                    end_time=session_end_utc,
                    limit=1000,
                )
                if candles:
                    all_candles = sorted((c for c in candles if c.source in ("BREEZE", "LIVE") and c.end_time <= min(utc_now(), session_end_utc)), key=lambda c: c.start_time)
            except Exception as ex:
                logger.warning("Historical service query error: %s", ex)

        # 2. Separate into warm-up vs session candles
        warmup_candles: list[Candle] = []
        session_candles: list[Candle] = []

        for c in all_candles:
            c_ist = c.start_time.astimezone(IST)
            if c_ist.date() < target_date or (c_ist.date() == target_date and c_ist.time() < session_start_ist.time()):
                warmup_candles.append(c)
            elif c_ist.date() == target_date and session_start_ist.time() <= c_ist.time() <= session_end_ist.time():
                session_candles.append(c)

        return warmup_candles, session_candles

    async def run_day_simulation(self, request: SimulationRequest) -> SimulationResult:
        """Replay actual bars without inventing historical option fills or PnL."""
        date_str = request.date or datetime.now(IST).strftime("%Y-%m-%d")
        warmup, session = await self._fetch_session_candles(date_str, request.instrument_id)
        futures_history = []
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if inst_svc:
            instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
            contracts = sorted((i for i in instruments if i.segment == "FUTURES" and i.expiry
                                and i.expiry >= date_str), key=lambda i: i.expiry)
            if contracts:
                warm_fut, day_fut = await self._fetch_session_candles(date_str, contracts[0].instrument_id)
                futures_history = warm_fut + day_fut
        overrides = request.overrides or ThresholdOverrides()
        if request.bypass_window:
            overrides = overrides.model_copy(update={"bypass_entry_window":True})
        cfg = self.tunables
        strat_a = TrendPullbackStrategy(
            adx_threshold=cfg.adx_threshold, rvol_threshold=cfg.rvol_threshold,
            ema_slope_threshold=cfg.ema_slope_threshold, min_confirmation_score=cfg.min_confirmation_score)
        strat_b = VolatilityBreakoutStrategy(rvol_threshold=cfg.rvol_threshold, adx_threshold=cfg.adx_threshold,
                                             min_confirmation_score=cfg.strat_b_min_confirmation,
                                             box_max_height_atr=cfg.box_max_height_atr,
                                             bb_width_percentile_threshold=cfg.bb_width_percentile_threshold,
                                             lookback_bars=cfg.compression_lookback_bars,
                                             max_age_bars=cfg.box_max_age_bars,
                                             breakout_buffer_atr=cfg.breakout_buffer_atr,
                                             max_extension_atr=cfg.breakout_max_extension_atr,
                                             entry_start=self.session_config.strategy_b_no_new_trade_before,
                                             entry_end=self.session_config.no_new_trade_after)
        timeline, logs = [], []
        running = list(warmup)
        start_h, start_m = map(int, self.session_config.no_new_trade_before.split(":"))
        end_h, end_m = map(int, self.session_config.no_new_trade_after.split(":"))
        for idx, bar in enumerate(session):
            running.append(bar)
            macro = self.resample_to_15m(running, request.instrument_id)
            futures = [c for c in futures_history if c.end_time <= bar.end_time]
            features = FeatureEngine.compute_all_features(running, macro, futures,
                                                          spot_price=bar.close, as_of=bar.end_time)
            diags_a = strat_a.diagnose(features, running, macro, overrides=overrides, futures_candles=futures)
            diags_b = strat_b.diagnose(features, running, overrides=overrides)
            clock = bar.end_time.astimezone(IST)
            minutes = clock.hour*60+clock.minute
            in_window = request.bypass_window or start_h*60+start_m <= minutes <= end_h*60+end_m
            event, details = None, None
            if (features.data_ready or features.breakout_data_ready) and in_window:
                sig_a = strat_a.evaluate(features, running, macro, futures, overrides) if cfg.trend_pullback_enabled else None
                sig_b = strat_b.evaluate(features, running, overrides=overrides) if cfg.volatility_breakout_enabled else None
                signal = sig_a or sig_b
                if signal:
                    event, details = "SIGNAL_ONLY", "Qualified signal; historical executable option quotes unavailable"
                    logs.append(DecisionLogEntry(id=f"SIM-{idx}", timestamp=bar.end_time, category="SETUP",
                                                 strategy=signal.strategy.value, message=details,
                                                 details=signal.model_dump(mode="json")))
            else:
                strat_a.reset(bar.end_time)
                strat_b.reset(bar.end_time)
                details = features.data_reason if not features.data_ready else "Outside entry window"
            timeline.append(SimulationBarSnapshot(
                bar_index=idx, timestamp=bar.end_time.isoformat(), ist_time=clock.strftime("%H:%M"),
                open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume, spot=bar.close,
                ema9_5m=features.ema9_5m, ema20_5m=features.ema20_5m,
                supertrend=features.supertrend_direction, adx_15m=features.adx_15m,
                rvol_5m=features.rvol_5m, bb_width_percentile=features.bb_width_percentile,
                strategy_a_phase=max(diags_a, key=lambda d: d.passed_count).phase_state,
                strategy_b_phase=max(diags_b, key=lambda d: d.passed_count).phase_state,
                event=event, event_details=details))
        return SimulationResult(
            session_date=date_str, total_bars_evaluated=len(session), total_trades=0, winning_trades=0,
            losing_trades=0, win_rate_pct=0, total_pnl=0, net_pnl=0, total_realized_r=0,
            max_drawdown_pnl=0, profit_factor=0, timeline=timeline, decision_logs=logs)
