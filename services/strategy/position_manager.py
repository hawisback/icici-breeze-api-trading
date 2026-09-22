"""Position Manager, Trailing Stop Engine, and Reversal Health Monitor.
Implements Sections 13.10, 14.6, 15, 17 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from datetime import datetime, timedelta, timezone

from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategyTunablesConfig,
    TradeDirection,
    TradeLifecycleState,
    utc_now,
)
from services.strategy.reason_codes import OPTION_EMERGENCY_STOP

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class StructuralRiskEstimate:
    """Underlying-thesis risk translated into option execution risk."""

    underlying_r: float
    option_loss_per_lot: float
    risk_budget: float
    lots: int
    quantity: int
    method: str
    rejection_reason: str | None = None


def underlying_r_for_price(
    direction: TradeDirection,
    entry_price: float,
    risk_points: float,
    exit_price: float,
) -> float:
    """Return the underlying R multiple for one completed exit fill."""
    if entry_price <= 0 or risk_points <= 0 or exit_price <= 0:
        return 0.0
    favorable = exit_price - entry_price if direction == TradeDirection.BULLISH else entry_price - exit_price
    return round(favorable / risk_points, 6)


def calculate_realized_trade_r(
    direction: TradeDirection,
    entry_price: float,
    risk_points: float,
    original_quantity: int,
    exits: Sequence[tuple[int, float]],
) -> float:
    """Calculate weighted underlying R across all executed exit quantities."""
    if original_quantity <= 0:
        return 0.0
    weighted = sum(
        quantity * underlying_r_for_price(direction, entry_price, risk_points, price)
        for quantity, price in exits if quantity > 0
    )
    return round(weighted / original_quantity, 4)


class UnderlyingRiskSizer:
    """Size options from futures structural R, never from an arbitrary premium stop."""

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self.config = risk_config or RiskConfig()

    def estimate_option_loss_per_lot(
        self,
        *,
        underlying_entry: float,
        underlying_stop: float,
        option_delta: float | None,
        lot_size: int,
        option_entry: float,
        multiplier: float = 1.0,
        fallback_loss_per_lot: float | None = None,
    ) -> tuple[float, str]:
        r = abs(underlying_entry - underlying_stop)
        if r <= 0 or lot_size <= 0 or option_entry <= 0:
            raise ValueError("invalid structural-risk sizing inputs")
        if option_delta is not None and 0 < abs(option_delta) <= 1:
            return abs(option_delta) * r * lot_size * multiplier, "DELTA_APPROXIMATION"
        if fallback_loss_per_lot is not None and fallback_loss_per_lot > 0:
            return fallback_loss_per_lot, "CONFIGURED_FALLBACK"
        raise ValueError("OPTION_RISK_UNAVAILABLE")

    def size(
        self,
        *,
        underlying_entry: float,
        underlying_stop: float,
        option_delta: float | None,
        lot_size: int,
        option_entry: float,
        account_equity: float | None = None,
        fallback_loss_per_lot: float | None = None,
    ) -> StructuralRiskEstimate:
        equity = account_equity or self.config.account_equity
        budget = equity * self.config.risk_per_trade_pct_of_account / 100.0
        loss_per_lot, method = self.estimate_option_loss_per_lot(
            underlying_entry=underlying_entry, underlying_stop=underlying_stop,
            option_delta=option_delta, lot_size=lot_size, option_entry=option_entry,
            fallback_loss_per_lot=fallback_loss_per_lot,
        )
        capital_lots = math.floor(self.config.max_trade_capital / (option_entry * lot_size))
        risk_lots = math.floor(budget / loss_per_lot) if loss_per_lot > 0 else 0
        lots = min(capital_lots, risk_lots, self.config.max_lots_per_trade)
        if lots < 1:
            return StructuralRiskEstimate(abs(underlying_entry - underlying_stop), loss_per_lot, budget, 0, 0, method, "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET")
        return StructuralRiskEstimate(abs(underlying_entry - underlying_stop), loss_per_lot, budget, lots, lots * lot_size, method)


class PositionManager:
    """Manages active position sizing, multi-level trailing stops, reversal scoring, and exits."""

    def __init__(
        self,
        risk_config: Optional[RiskConfig] = None,
        session_config: Optional[SessionTimersConfig] = None,
        bull_derivatives_threshold: float = 2.0,
        bear_derivatives_threshold: float = 2.0,
        strategy_config: Optional[StrategyTunablesConfig] = None,
    ) -> None:
        self.risk_config = risk_config or RiskConfig()
        self.session_config = session_config or SessionTimersConfig()
        self.bull_derivatives_threshold = bull_derivatives_threshold
        self.bear_derivatives_threshold = bear_derivatives_threshold
        self.strategy_config = strategy_config or StrategyTunablesConfig()

    def update_strategy_a_position(
        self,
        trade: ActiveTrade,
        current_underlying_price: float,
        current_option_price: float | None,
        as_of=None,
    ) -> tuple[ActiveTrade, Optional[str]]:
        """Manage Strategy A from futures structural R only."""
        entry_price = trade.underlying_entry_price or trade.entry_spot_price
        initial_r = trade.underlying_r or trade.initial_r_points
        if current_underlying_price <= 0 or entry_price <= 0 or initial_r <= 0:
            return trade, "INVALID_INITIAL_UNDERLYING_R"
        trade.underlying_entry_price = entry_price
        trade.underlying_current_price = current_underlying_price
        trade.current_spot_price = current_underlying_price
        option_price = float(current_option_price or 0.0)
        if option_price > 0:
            trade.current_option_price = option_price
        directional_points = (
            current_underlying_price - entry_price
            if trade.direction == TradeDirection.BULLISH
            else entry_price - current_underlying_price
        )
        trade.current_r = round(directional_points / initial_r, 4)
        trade.peak_r = max(trade.peak_r, trade.current_r)
        trade.mfe_points = max(trade.mfe_points, directional_points)
        trade.mae_points = min(trade.mae_points, directional_points)
        if option_price > 0:
            trade.unrealized_pnl = round((option_price - trade.entry_option_price) * trade.quantity, 2)
        if as_of is not None and self.is_strategy_a_force_exit_time(as_of):
            return trade, "SESSION_FORCE_SQUARE_OFF_1515"
        if option_price > 0 and option_price <= trade.option_hard_stop_price:
            return trade, OPTION_EMERGENCY_STOP

        # The protective stop is monotonic.  Before activation it is the
        # initial structural stop; at +1R it tightens to breakeven plus the
        # configured buffer and is then used for the actual exit check.
        if trade.current_trailing_stop <= 0:
            trade.current_trailing_stop = trade.initial_structural_stop
        activation = self.strategy_config.trailing_activation_r
        if trade.peak_r >= activation:
            trade.state = TradeLifecycleState.PROTECTED_BREAKEVEN
            if trade.direction == TradeDirection.BULLISH:
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, entry_price + self.risk_config.breakeven_buffer_points), 2)
            else:
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, entry_price - self.risk_config.breakeven_buffer_points), 2)
        activated = trade.peak_r >= activation
        if trade.direction == TradeDirection.BULLISH and current_underlying_price <= trade.current_trailing_stop:
            return trade, "UNDERLYING_TRAILING_STOP" if activated else "UNDERLYING_STRUCTURAL_STOP"
        if trade.direction == TradeDirection.BEARISH and current_underlying_price >= trade.current_trailing_stop:
            return trade, "UNDERLYING_TRAILING_STOP" if activated else "UNDERLYING_STRUCTURAL_STOP"

        if trade.peak_r >= self.strategy_config.t1_r and not trade.t1_reached:
            trade.t1_reached = True
            trade.state = TradeLifecycleState.PROFIT_LOCKED
            if trade.direction == TradeDirection.BULLISH:
                trade.current_trailing_stop = round(
                    max(trade.current_trailing_stop, entry_price + 0.50 * initial_r), 2
                )
            else:
                trade.current_trailing_stop = round(
                    min(trade.current_trailing_stop, entry_price - 0.50 * initial_r), 2
                )
            original_quantity = trade.initial_quantity or trade.quantity
            original_lots = max(1, original_quantity // trade.lot_size)
            partial_lots = original_lots // 2
            if partial_lots < 1:
                trade.remaining_quantity = original_quantity
                trade.partial_exit_reason = "T1_REACHED_NO_PARTIAL_ONE_LOT"
                return trade, "T1_REACHED_NO_PARTIAL_ONE_LOT"
            partial_quantity = partial_lots * trade.lot_size
            trade.t1_exit_quantity = partial_quantity
            trade.remaining_quantity = original_quantity - trade.partial_exit_filled_quantity
            trade.t1_exit_pending = True
            trade.t1_decision_underlying_price = current_underlying_price
            trade.t1_decision_r = trade.current_r
            trade.partial_exit_reason = "T1_PARTIAL_EXIT"
            return trade, "T1_PARTIAL_EXIT"
        if trade.t1_exit_pending and trade.t1_exit_quantity > trade.partial_exit_filled_quantity:
            return trade, "T1_PARTIAL_EXIT"
        if trade.peak_r >= self.strategy_config.runner_target_reference_r:
            trade.state = TradeLifecycleState.RUNNER_MODE
        return trade, None

    def apply_t1_partial_fill(
        self,
        trade: ActiveTrade,
        *,
        raw_bid: float,
        executable_price: float,
        slippage_points: float,
        filled_at: datetime,
    ) -> ActiveTrade:
        """Apply a confirmed T1 option fill; the decision itself never mutates quantity."""
        quantity = trade.t1_exit_quantity
        original_quantity = trade.initial_quantity or trade.quantity
        if quantity <= 0 or quantity % trade.lot_size != 0:
            raise ValueError("Strategy A partial exit quantity must be a positive whole-lot quantity")
        if trade.partial_exit_filled_quantity + quantity > original_quantity:
            raise ValueError("Strategy A partial exit exceeds original quantity")
        if raw_bid <= 0 or executable_price <= 0:
            raise ValueError("Strategy A partial exit requires a valid executable bid")
        if trade.partial_exit_filled_quantity > 0:
            return trade
        trade.partial_exit_filled_quantity = quantity
        trade.partial_exit_raw_bid = round(raw_bid, 2)
        trade.partial_exit_price = round(executable_price, 2)
        trade.partial_exit_slippage_points = round(slippage_points, 4)
        trade.partial_exit_time = filled_at
        trade.partial_exit_reason = "T1_REACHED_PARTIAL_EXIT"
        trade.t1_exit_pending = False
        trade.remaining_quantity = original_quantity - quantity
        trade.quantity = trade.remaining_quantity
        trade.lots = trade.remaining_quantity // trade.lot_size
        trade.t1_realized_r = trade.t1_decision_r
        return trade

    def calculate_position_size(
        self,
        entry_premium: float,
        account_equity: float = 500000.0,
        lot_size: int = 0,
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

        lots = min(capital_lots, risk_lots, cfg.max_lots_per_trade)
        if lots < 1:
            # Critical rule Section 14: if final_lots < 1: NO TRADE. Never use max(1, ...)
            return 0, 0
        quantity = lots * lot_size
        return lots, quantity

    def calculate_reversal_score(
        self,
        trade: ActiveTrade,
        features: MarketFeatures,
    ) -> int:
        """Explicit 8-Factor Adverse-Health / Reversal Score (Section 18).
        Returns integer 0-8 measuring signs of thesis failure.
        """
        score = 0
        spot = features.closed_5m_price
        if spot is None:
            return 0

        if trade.direction == TradeDirection.BULLISH:
            # 1. completed 5m close < EMA9_5m
            if spot < features.ema9_5m:
                score += 1
            # 2. completed 5m close < EMA20_5m
            if spot < features.ema20_5m:
                score += 1
            # 3. NIFTY futures < futures VWAP
            if features.futures_price < features.futures_vwap:
                score += 1
            # 4. RSI5m < 48
            if features.rsi_5m < 48.0:
                score += 1
            # 5. -DI > +DI
            if features.minus_di_5m > features.plus_di_5m:
                score += 1
            # 6. 5m Supertrend becomes bearish
            if features.supertrend_direction == "BEARISH":
                score += 1
            # 7. Bearish derivatives score reaches threshold
            adverse_derivatives = features.breakout_bear_derivatives_score if trade.strategy == StrategyName.VOLATILITY_BREAKOUT else features.bear_derivatives_score
            if adverse_derivatives >= self.bear_derivatives_threshold:
                score += 1
            # 8. completed 5m close breaks pullback swing low / structural level
            if (features.futures_buildup in ("SHORT_BUILDUP", "LONG_UNWINDING") if trade.strategy == StrategyName.VOLATILITY_BREAKOUT
                    else features.swing_low_5m is not None and spot < features.swing_low_5m):
                score += 1
        else:
            # Bearish trade
            # 1. completed 5m close > EMA9_5m
            if spot > features.ema9_5m:
                score += 1
            # 2. completed 5m close > EMA20_5m
            if spot > features.ema20_5m:
                score += 1
            # 3. NIFTY futures > futures VWAP
            if features.futures_price > features.futures_vwap:
                score += 1
            # 4. RSI5m > 52
            if features.rsi_5m > 52.0:
                score += 1
            # 5. +DI > -DI
            if features.plus_di_5m > features.minus_di_5m:
                score += 1
            # 6. 5m Supertrend becomes bullish
            if features.supertrend_direction == "BULLISH":
                score += 1
            # 7. Bullish derivatives score reaches threshold
            adverse_derivatives = features.breakout_bull_derivatives_score if trade.strategy == StrategyName.VOLATILITY_BREAKOUT else features.bull_derivatives_score
            if adverse_derivatives >= self.bull_derivatives_threshold:
                score += 1
            # 8. completed 5m close breaks pullback swing high / structural level
            if (features.futures_buildup in ("LONG_BUILDUP", "SHORT_COVERING") if trade.strategy == StrategyName.VOLATILITY_BREAKOUT
                    else features.swing_high_5m is not None and spot > features.swing_high_5m):
                score += 1

        return min(8, score)

    def is_force_exit_time(self, as_of=None) -> bool:
        """Checks if current IST time is past force_exit_time (e.g. 15:20)."""
        now_ist = as_of.astimezone(IST) if as_of else datetime.now(IST)
        exit_hour, exit_min = map(int, self.session_config.force_exit_time.split(":"))
        return (now_ist.hour > exit_hour) or (now_ist.hour == exit_hour and now_ist.minute >= exit_min)

    def is_strategy_a_force_exit_time(self, as_of: datetime) -> bool:
        """Evaluate Strategy A's explicit 15:15 schedule, never shared 15:20."""
        now_ist = as_of.astimezone(IST)
        exit_hour, exit_min = map(int, self.strategy_config.forced_exit_time.split(":"))
        return (now_ist.hour > exit_hour) or (now_ist.hour == exit_hour and now_ist.minute >= exit_min)

    def is_within_strategy_a_entry_window(self, as_of: datetime) -> bool:
        now_ist = as_of.astimezone(IST)
        start_h, start_m = map(int, self.strategy_config.entry_session_start.split(":"))
        end_h, end_m = map(int, self.strategy_config.entry_session_end.split(":"))
        current = now_ist.hour * 60 + now_ist.minute
        return start_h * 60 + start_m <= current <= end_h * 60 + end_m

    def is_within_entry_window(self) -> bool:
        """Checks if current IST time allows new trade entries (09:20/09:30 - 14:45)."""
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
        current_option_price: float | None,
        features: MarketFeatures,
        as_of=None,
    ) -> tuple[ActiveTrade, Optional[str]]:
        """Evaluates active trade against stops, trailing transitions, reversal scores, and time square-off.
        
        Returns:
            (updated_trade, exit_reason_if_triggered)
        """
        if trade.strategy == StrategyName.TREND_PULLBACK:
            underlying = features.futures_price
            return self.update_strategy_a_position(trade, underlying, current_option_price, as_of)

        spot = features.spot_price
        trade.current_spot_price = spot
        trade.current_option_price = current_option_price

        # Update PnL
        option_price = float(current_option_price or 0.0)
        unrealized = (option_price - trade.entry_option_price) * trade.quantity
        trade.unrealized_pnl = round(unrealized, 2)

        # 1. R Multiple tracking
        r_points = trade.initial_r_points
        if r_points <= 0:
            return trade, "INVALID_INITIAL_R"
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

        new_bar = (features.closed_5m_time is not None and features.closed_5m_price is not None
                   and 0 <= (features.timestamp-features.closed_5m_time).total_seconds() < 300
                   and features.closed_5m_time > trade.entry_time
                   and (trade.last_managed_bar is None or features.closed_5m_time > trade.last_managed_bar))
        closed = features.closed_5m_price
        if new_bar:
            trade.last_managed_bar = features.closed_5m_time
            trade.highest_close_since_entry = max(trade.highest_close_since_entry or trade.entry_spot_price, closed)
            trade.lowest_close_since_entry = min(trade.lowest_close_since_entry or trade.entry_spot_price, closed)

        # 2. Invalidation requires a completed 5m close, never an intrabar quote.
        if trade.direction == TradeDirection.BULLISH and trade.pullback_swing_low is not None:
            if new_bar and closed < trade.pullback_swing_low:
                return trade, f"IMMEDIATE_THESIS_INVALIDATION (Spot {spot} < Pullback Low {trade.pullback_swing_low})"
        elif trade.direction == TradeDirection.BEARISH and trade.pullback_swing_high is not None:
            if new_bar and closed > trade.pullback_swing_high:
                return trade, f"IMMEDIATE_THESIS_INVALIDATION (Spot {spot} > Pullback High {trade.pullback_swing_high})"

        # 2b. Check False Breakout for Strategy B (Section 23)
        if trade.strategy == StrategyName.VOLATILITY_BREAKOUT and new_bar:
            atr_ref = trade.atr_at_lock if trade.atr_at_lock is not None else features.atr_5m
            if trade.direction == TradeDirection.BULLISH and trade.box_high is not None:
                if closed < (trade.box_high - 0.10 * atr_ref):
                    return trade, f"FALSE_BREAKOUT_EXIT (Spot {spot} < BoxHigh {trade.box_high} - 0.10*ATR {round(0.10 * atr_ref, 2)})"
                if closed < trade.box_high:
                    trade.consecutive_inside_box_closes += 1
                    if trade.consecutive_inside_box_closes >= 2:
                        return trade, f"FALSE_BREAKOUT_EXIT (2 consecutive closes inside box < {trade.box_high})"
                else:
                    trade.consecutive_inside_box_closes = 0
            elif trade.direction == TradeDirection.BEARISH and trade.box_low is not None:
                if closed > (trade.box_low + 0.10 * atr_ref):
                    return trade, f"FALSE_BREAKOUT_EXIT (Spot {spot} > BoxLow {trade.box_low} + 0.10*ATR {round(0.10 * atr_ref, 2)})"
                if closed > trade.box_low:
                    trade.consecutive_inside_box_closes += 1
                    if trade.consecutive_inside_box_closes >= 2:
                        return trade, f"FALSE_BREAKOUT_EXIT (2 consecutive closes inside box > {trade.box_low})"
                else:
                    trade.consecutive_inside_box_closes = 0

        # 3. Check Emergency Option Hard Stop (-25% default) — only when option price is valid
        if option_price > 0 and option_price <= trade.option_hard_stop_price:
            return trade, f"OPTION_HARD_STOP_HIT (LTP {current_option_price} <= SL {trade.option_hard_stop_price})"

        # 4. Check Structural Spot Stop — only when spot price is valid (not 0 / stale)
        if trade.direction == TradeDirection.BULLISH:
            if spot > 0 and spot <= trade.current_trailing_stop:
                return trade, f"STRUCTURAL_SPOT_STOP_BREACHED (Spot {spot} <= SL {trade.current_trailing_stop})"
        else:
            if spot > 0 and spot >= trade.current_trailing_stop:
                return trade, f"STRUCTURAL_SPOT_STOP_BREACHED (Spot {spot} >= SL {trade.current_trailing_stop})"

        # 5. Check Session Force Square-off (15:20 IST)
        if self.is_force_exit_time(as_of):
            return trade, "SESSION_FORCE_SQUARE_OFF_1520"

        if not new_bar:
            return trade, None

        # 6. Reversal Health Score Actions (Section 18.3)
        reversal_score = self.calculate_reversal_score(trade, features)
        trade.reversal_score = reversal_score

        if reversal_score >= 4:
            return trade, f"ADVERSE_HEALTH_SCORE_CRITICAL (Score {reversal_score}/8 >= 4)"
        if reversal_score >= 3 and current_r < 1.0:
            return trade, f"ADVERSE_HEALTH_SCORE_EARLY_EXIT (Score {reversal_score}/8 with R {current_r} < 1.0R)"
        if reversal_score == 2:
            # Tighten only to a valid stop on the protective side of the close.
            if trade.direction == TradeDirection.BULLISH:
                tightened = min(features.ema9_5m - .25*features.atr_5m, closed - .15*features.atr_5m)
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, tightened), 2)
            else:
                tightened = max(features.ema9_5m + .25*features.atr_5m, closed + .15*features.atr_5m)
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, tightened), 2)

        if reversal_score == 3 and current_r >= 1:
            if trade.direction == TradeDirection.BULLISH:
                candidates = [features.ema9_5m - .25*features.atr_5m, features.swing_low_5m]
                valid = [x for x in candidates if x is not None and x < closed]
                trade.current_trailing_stop = max([trade.current_trailing_stop] + valid)
            else:
                candidates = [features.ema9_5m + .25*features.atr_5m, features.swing_high_5m]
                valid = [x for x in candidates if x is not None and x > closed]
                trade.current_trailing_stop = min([trade.current_trailing_stop] + valid)

        # 7. Multi-Level Trailing Stop Ladder (Section 17)
        atr = features.atr_5m
        if trade.direction == TradeDirection.BULLISH:
            if trade.peak_r >= 2.0:
                # Runner mode
                trade.state = TradeLifecycleState.RUNNER_MODE
                runner_stop = max(
                    trade.current_trailing_stop,
                    features.ema9_5m - (0.25 * atr),
                    trade.highest_close_since_entry - atr,
                    features.swing_low_5m if features.swing_low_5m is not None else trade.current_trailing_stop,
                )
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, runner_stop), 2)
            elif trade.peak_r >= 1.5:
                # Lock +0.5R
                trade.state = TradeLifecycleState.PROFIT_LOCKED
                lock_stop = trade.entry_spot_price + (0.50 * r_points)
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, lock_stop), 2)
            elif trade.peak_r >= 1.0:
                # Protected Breakeven: entry + 2.0
                trade.state = TradeLifecycleState.PROTECTED_BREAKEVEN
                breakeven_stop = trade.entry_spot_price + self.risk_config.breakeven_buffer_points
                trade.current_trailing_stop = round(max(trade.current_trailing_stop, breakeven_stop), 2)
        else:
            if trade.peak_r >= 2.0:
                trade.state = TradeLifecycleState.RUNNER_MODE
                runner_stop = min(
                    trade.current_trailing_stop,
                    features.ema9_5m + (0.25 * atr),
                    trade.lowest_close_since_entry + atr,
                    features.swing_high_5m if features.swing_high_5m is not None else trade.current_trailing_stop,
                )
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, runner_stop), 2)
            elif trade.peak_r >= 1.5:
                trade.state = TradeLifecycleState.PROFIT_LOCKED
                lock_stop = trade.entry_spot_price - (0.50 * r_points)
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, lock_stop), 2)
            elif trade.peak_r >= 1.0:
                trade.state = TradeLifecycleState.PROTECTED_BREAKEVEN
                breakeven_stop = trade.entry_spot_price - self.risk_config.breakeven_buffer_points
                trade.current_trailing_stop = round(min(trade.current_trailing_stop, breakeven_stop), 2)

        return trade, None
