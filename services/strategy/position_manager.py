"""Position Manager, Trailing Stop Engine, and Reversal Health Monitor.
Implements Sections 13.10, 14.6, 15, 17 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional
from datetime import datetime, timedelta, timezone

from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    TradeDirection,
    TradeLifecycleState,
    utc_now,
)

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


class PositionManager:
    """Manages active position sizing, multi-level trailing stops, reversal scoring, and exits."""

    def __init__(
        self,
        risk_config: Optional[RiskConfig] = None,
        session_config: Optional[SessionTimersConfig] = None,
    ) -> None:
        self.risk_config = risk_config or RiskConfig()
        self.session_config = session_config or SessionTimersConfig()

    def calculate_position_size(
        self,
        entry_premium: float,
        account_equity: float = 500000.0,
        lot_size: int = 25,
    ) -> tuple[int, int]:
        """Calculates allowed lots and total quantity based on capital cap and risk limits.
        
        Returns:
            (lots, total_quantity)
        """
        cfg = self.risk_config
        capital_per_lot = entry_premium * lot_size
        if capital_per_lot <= 0:
            return 0, 0

        # Capital limited lots
        capital_lots = math.floor(cfg.max_trade_capital / capital_per_lot)

        # Risk limited lots based on option hard stop
        risk_budget = account_equity * (cfg.risk_per_trade_pct_of_account / 100.0)
        hard_stop_decimal = cfg.option_hard_stop_pct / 100.0
        premium_risk_per_lot = entry_premium * hard_stop_decimal * lot_size

        risk_lots = math.floor(risk_budget / premium_risk_per_lot) if premium_risk_per_lot > 0 else capital_lots

        lots = min(capital_lots, risk_lots)
        lots = max(1, lots)  # at least 1 lot if valid capital
        quantity = lots * lot_size
        return lots, quantity

    def calculate_reversal_score(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
    ) -> int:
        """Shared Reversal / Trade-Health score (Section 15).
        Returns integer 0-10 measuring signs of thesis failure.
        """
        score = 0
        spot = features.spot_price

        if trade.direction == TradeDirection.BULLISH:
            if spot < features.ema9_5m:
                score += 1
            if features.futures_price < features.futures_vwap:
                score += 1
            if features.supertrend_direction == "BEARISH":
                score += 1
            if features.rsi_5m < 48.0:
                score += 1
            if features.minus_di_15m > features.plus_di_15m:
                score += 1
            if features.futures_buildup in ("SHORT_BUILDUP", "LONG_UNWINDING"):
                score += 1
            if features.bear_derivatives_score >= 2.0:
                score += 1
            if spot < trade.entry_spot_price:
                score += 1
        else:
            # Bearish trade
            if spot > features.ema9_5m:
                score += 1
            if features.futures_price > features.futures_vwap:
                score += 1
            if features.supertrend_direction == "BULLISH":
                score += 1
            if features.rsi_5m > 52.0:
                score += 1
            if features.plus_di_15m > features.minus_di_15m:
                score += 1
            if features.futures_buildup in ("LONG_BUILDUP", "SHORT_COVERING"):
                score += 1
            if features.bull_derivatives_score >= 2.0:
                score += 1
            if spot > trade.entry_spot_price:
                score += 1

        return min(10, score)

    def is_force_exit_time(self) -> bool:
        """Checks if current IST time is past force_exit_time (e.g. 15:20)."""
        now_ist = datetime.now(IST)
        exit_hour, exit_min = map(int, self.session_config.force_exit_time.split(":"))
        return (now_ist.hour > exit_hour) or (now_ist.hour == exit_hour and now_ist.minute >= exit_min)

    def is_within_entry_window(self) -> bool:
        """Checks if current IST time allows new trade entries (09:30 - 14:45)."""
        now_ist = datetime.now(IST)
        start_h, start_m = map(int, self.session_config.no_new_trade_before.split(":"))
        end_h, end_m = map(int, self.session_config.no_new_trade_after.split(":"))

        current_mins = now_ist.hour * 60 + now_ist.minute
        start_mins = start_h * 60 + start_m
        end_mins = end_h * 60 + end_m

        return start_mins <= current_mins <= end_mins

    def update_position(
        self,
        trade: ActiveTrade,
        current_option_price: float,
        features: MarketFeatures,
    ) -> tuple[ActiveTrade, Optional[str]]:
        """Evaluates active trade against stops, trailing transitions, reversal scores, and time square-off.
        
        Returns:
            (updated_trade, exit_reason_if_triggered)
        """
        spot = features.spot_price
        trade.current_spot_price = spot
        trade.current_option_price = current_option_price

        # Update PnL
        unrealized = (current_option_price - trade.entry_option_price) * trade.quantity
        trade.unrealized_pnl = round(unrealized, 2)

        # 1. R Multiple tracking
        r_points = max(1.0, trade.initial_r_points)
        if trade.direction == TradeDirection.BULLISH:
            current_r = round((spot - trade.entry_spot_price) / r_points, 2)
            mfe = round(max(trade.mfe_points, spot - trade.entry_spot_price), 2)
            mae = round(min(trade.mae_points, spot - trade.entry_spot_price), 2)
        else:
            current_r = round((trade.entry_spot_price - spot) / r_points, 2)
            mfe = round(max(trade.mfe_points, trade.entry_spot_price - spot), 2)
            mae = round(min(trade.mae_points, trade.entry_spot_price - spot), 2)

        trade.current_r = current_r
        trade.peak_r = max(trade.peak_r, current_r)
        trade.mfe_points = mfe
        trade.mae_points = mae

        # 2. Reversal Health Score
        reversal_score = self.calculate_reversal_score(trade, features)
        trade.reversal_score = reversal_score

        # 3. Check Emergency Option Hard Stop (-25% default)
        if current_option_price <= trade.option_hard_stop_price:
            return trade, f"OPTION_HARD_STOP_HIT (LTP {current_option_price} <= SL {trade.option_hard_stop_price})"

        # 4. Check Structural Spot Stop
        if trade.direction == TradeDirection.BULLISH:
            if spot <= trade.current_trailing_stop:
                return trade, f"STRUCTURAL_SPOT_STOP_BREACHED (Spot {spot} <= SL {trade.current_trailing_stop})"
        else:
            if spot >= trade.current_trailing_stop:
                return trade, f"STRUCTURAL_SPOT_STOP_BREACHED (Spot {spot} >= SL {trade.current_trailing_stop})"

        # 5. Check Session Force Square-off (15:20 IST)
        if self.is_force_exit_time():
            return trade, "SESSION_FORCE_SQUARE_OFF_1520"

        # 6. Check Thesis Reversal Action Matrix
        if reversal_score >= 4:
            return trade, f"THESIS_REVERSAL_CRITICAL (Score {reversal_score}/10 >= 4)"
        if reversal_score == 3 and current_r < 1.0:
            return trade, f"THESIS_REVERSAL_EXIT (Score 3/10 with R {current_r} < 1.0R)"

        # 7. Multi-Level Trailing Stop Transitions
        # Bullish Trail Logic
        if trade.direction == TradeDirection.BULLISH:
            if current_r >= 2.0:
                # Runner mode
                trade.state = TradeLifecycleState.RUNNER_MODE
                # Trail stop: EMA9 - 0.25*ATR or highest close - 1.0*ATR
                atr = features.atr_5m
                runner_stop = max(
                    trade.current_trailing_stop,
                    features.ema9_5m - (0.25 * atr),
                    spot - (1.0 * atr),
                )
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, runner_stop), 2)
            elif current_r >= 1.5:
                # Lock +0.5R
                trade.state = TradeLifecycleState.PROFIT_LOCKED
                lock_stop = trade.entry_spot_price + (0.50 * r_points)
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, lock_stop), 2)
            elif current_r >= 1.0:
                # Protected Breakeven
                trade.state = TradeLifecycleState.PROTECTED_BREAKEVEN
                breakeven_stop = trade.entry_spot_price + 2.0  # cost buffer
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, breakeven_stop), 2)

        else:
            # Bearish Trail Logic
            if current_r >= 2.0:
                trade.state = TradeLifecycleState.RUNNER_MODE
                atr = features.atr_5m
                runner_stop = min(
                    trade.current_trailing_stop,
                    features.ema9_5m + (0.25 * atr),
                    spot + (1.0 * atr),
                )
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, runner_stop), 2)
            elif current_r >= 1.5:
                trade.state = TradeLifecycleState.PROFIT_LOCKED
                lock_stop = trade.entry_spot_price - (0.50 * r_points)
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, lock_stop), 2)
            elif current_r >= 1.0:
                trade.state = TradeLifecycleState.PROTECTED_BREAKEVEN
                breakeven_stop = trade.entry_spot_price - 2.0
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, breakeven_stop), 2)

        return trade, None
