"""STRATEGY A — Trend Pullback Continuation.
Implements Section 13 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import logging
from typing import Optional
from libs.contracts.models import Candle
from services.strategy.models import (
    MarketFeatures,
    OptionType,
    StrategyName,
    StrategySignal,
    StrategyTriggerDiagnostics,
    ThresholdOverrides,
    TradeDirection,
    TriggerCondition,
    utc_now,
)

logger = logging.getLogger(__name__)


class TrendPullbackStrategy:
    """Strategy A: Trend Pullback Continuation.
    
    Trades high-confidence pullback continuations in an established trend.
    Bullish setup buys Call; Bearish setup buys Put.
    """

    def __init__(self, adx_threshold: float = 20.0, rvol_threshold: float = 1.20) -> None:
        self.adx_threshold = adx_threshold
        self.rvol_threshold = rvol_threshold

    def evaluate(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        futures_candles: Optional[list[Candle]] = None,
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates closed candles against Trend Pullback Continuation setup rules."""
        if not candles_5m or len(candles_5m) < 6:
            return None

        # Check Bullish Setup
        bull_signal = self._evaluate_bullish(features, candles_5m, candles_15m, overrides=overrides)
        if bull_signal:
            return bull_signal

        # Check Bearish Setup
        bear_signal = self._evaluate_bearish(features, candles_5m, candles_15m, overrides=overrides)
        if bear_signal:
            return bear_signal

        return None

    def _evaluate_bullish(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)
        latest_5m = candles_5m[-1]
        prev_5m = candles_5m[-2]

        adx_target = overrides.adx_threshold if (overrides and overrides.adx_threshold is not None) else self.adx_threshold
        deriv_target = overrides.bull_derivatives_score if (overrides and overrides.bull_derivatives_score is not None) else 2.0

        # 1. Bullish Regime Evaluation
        c_ema_cross = features.ema20_15m > features.ema50_15m
        c_ema_slope = features.ema20_slope_15m > 0
        c_close_ema20 = spot > features.ema20_15m
        c_adx = features.adx_15m >= adx_target
        c_di = features.plus_di_15m > features.minus_di_15m
        c_supertrend = features.supertrend_direction == "BULLISH"
        c_fut_vwap = features.futures_price > features.futures_vwap
        c_deriv_score = features.bull_derivatives_score >= deriv_target

        regime_bullish = (
            c_ema_cross
            and c_ema_slope
            and c_close_ema20
            and c_adx
            and c_di
            and c_supertrend
            and c_fut_vwap
            and c_deriv_score
        )

        # 2. Bullish Pullback Detection
        # Identify local impulse high in the last 15 bars
        lookback_bars = min(15, len(candles_5m) - 1)
        recent_candles = candles_5m[-lookback_bars:-1]
        highs = [c.high for c in recent_candles]
        impulse_high = max(highs) if highs else latest_5m.high
        impulse_idx = -1
        for i, c in enumerate(reversed(recent_candles)):
            if c.high == impulse_high:
                impulse_idx = i + 1  # bars since impulse high
                break

        bars_since_impulse = impulse_idx if impulse_idx != -1 else 3
        c_pullback_bars = 2 <= bars_since_impulse <= 8

        # Pullback swing low (lowest low between impulse and trigger)
        pullback_candles = candles_5m[-bars_since_impulse:]
        pullback_low = min(c.low for c in pullback_candles)
        impulse_low = min(c.low for c in recent_candles)
        impulse_height = max(1.0, impulse_high - impulse_low)
        pullback_depth = (impulse_high - pullback_low) / impulse_height
        c_pullback_depth = pullback_depth <= 0.65

        # Proximity to 5m EMA20 or VWAP
        c_support_retest = (
            abs(pullback_low - features.ema20_5m) <= 1.2 * atr
            or pullback_low >= (features.futures_vwap - 30.0)
        )

        pullback_detected = c_pullback_bars and c_pullback_depth and c_support_retest

        # 3. Bullish Entry Trigger Candle
        c_trigger_high = latest_5m.close > prev_5m.high
        c_trigger_ema9 = latest_5m.close > features.ema9_5m
        c_rsi = features.rsi_5m >= 50.0
        c_candle_range = (latest_5m.high - latest_5m.low) <= (1.85 * atr)

        trigger_fired = (
            c_trigger_high
            and c_trigger_ema9
            and c_rsi
            and c_candle_range
        )

        # 4. Structural Risk & R Calculation
        candidate_stop = round(pullback_low - (0.15 * atr), 2)
        r_points = round(spot - candidate_stop, 2)
        c_risk_band = (0.45 * atr) <= r_points <= (1.60 * atr)

        conditions = {
            "regime_ema_cross": c_ema_cross,
            "regime_ema_slope": c_ema_slope,
            "regime_close_above_ema20": c_close_ema20,
            "regime_adx_trend": c_adx,
            "regime_plus_di_dom": c_di,
            "regime_supertrend_bullish": c_supertrend,
            "regime_futures_above_vwap": c_fut_vwap,
            "derivatives_confirmation": c_deriv_score,
            "pullback_bars_valid": c_pullback_bars,
            "pullback_depth_healthy": c_pullback_depth,
            "pullback_support_proximity": c_support_retest,
            "trigger_close_above_prev_high": c_trigger_high,
            "trigger_close_above_ema9": c_trigger_ema9,
            "trigger_rsi_momentum": c_rsi,
            "trigger_candle_range_normal": c_candle_range,
            "risk_r_band_valid": c_risk_band,
        }

        all_passed = regime_bullish and pullback_detected and trigger_fired and c_risk_band

        if not all_passed:
            # We also record candidate state when trigger is close to firing
            return None

        return StrategySignal(
            signal_id=f"SIG-A-BULL-{int(utc_now().timestamp())}",
            strategy=StrategyName.TREND_PULLBACK,
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            timestamp=utc_now(),
            spot_reference_price=spot,
            structural_stop=candidate_stop,
            r_points=r_points,
            derivatives_score=features.bull_derivatives_score,
            features_snapshot={
                "conditions": conditions,
                "pullback_low": pullback_low,
                "impulse_high": impulse_high,
                "pullback_depth": round(pullback_depth, 2),
                "r_points": r_points,
                "atr": atr,
            },
            passed=True,
        )

    def _evaluate_bearish(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)
        latest_5m = candles_5m[-1]
        prev_5m = candles_5m[-2]

        adx_target = overrides.adx_threshold if (overrides and overrides.adx_threshold is not None) else self.adx_threshold
        deriv_target = overrides.bear_derivatives_score if (overrides and overrides.bear_derivatives_score is not None) else 2.0

        # 1. Bearish Regime Evaluation
        c_ema_cross = features.ema20_15m < features.ema50_15m
        c_ema_slope = features.ema20_slope_15m < 0
        c_close_ema20 = spot < features.ema20_15m
        c_adx = features.adx_15m >= adx_target
        c_di = features.minus_di_15m > features.plus_di_15m
        c_supertrend = features.supertrend_direction == "BEARISH"
        c_fut_vwap = features.futures_price < features.futures_vwap
        c_deriv_score = features.bear_derivatives_score >= deriv_target

        regime_bearish = (
            c_ema_cross
            and c_ema_slope
            and c_close_ema20
            and c_adx
            and c_di
            and c_supertrend
            and c_fut_vwap
            and c_deriv_score
        )

        # 2. Bearish Pullback Detection
        lookback_bars = min(15, len(candles_5m) - 1)
        recent_candles = candles_5m[-lookback_bars:-1]
        lows = [c.low for c in recent_candles]
        impulse_low = min(lows) if lows else latest_5m.low
        impulse_idx = -1
        for i, c in enumerate(reversed(recent_candles)):
            if c.low == impulse_low:
                impulse_idx = i + 1
                break

        bars_since_impulse = impulse_idx if impulse_idx != -1 else 3
        c_pullback_bars = 2 <= bars_since_impulse <= 8

        # Pullback swing high (highest high between impulse and trigger)
        pullback_candles = candles_5m[-bars_since_impulse:]
        pullback_high = max(c.high for c in pullback_candles)
        impulse_high = max(c.high for c in recent_candles)
        impulse_height = max(1.0, impulse_high - impulse_low)
        pullback_depth = (pullback_high - impulse_low) / impulse_height
        c_pullback_depth = pullback_depth <= 0.65

        # Proximity to 5m EMA20 or VWAP
        c_support_retest = (
            abs(pullback_high - features.ema20_5m) <= 1.2 * atr
            or pullback_high <= (features.futures_vwap + 30.0)
        )

        pullback_detected = c_pullback_bars and c_pullback_depth and c_support_retest

        # 3. Bearish Entry Trigger Candle
        c_trigger_low = latest_5m.close < prev_5m.low
        c_trigger_ema9 = latest_5m.close < features.ema9_5m
        c_rsi = features.rsi_5m <= 50.0
        c_candle_range = (latest_5m.high - latest_5m.low) <= (1.85 * atr)

        trigger_fired = (
            c_trigger_low
            and c_trigger_ema9
            and c_rsi
            and c_candle_range
        )

        # 4. Structural Risk & R Calculation
        candidate_stop = round(pullback_high + (0.15 * atr), 2)
        r_points = round(candidate_stop - spot, 2)
        c_risk_band = (0.45 * atr) <= r_points <= (1.60 * atr)

        conditions = {
            "regime_ema_cross": c_ema_cross,
            "regime_ema_slope": c_ema_slope,
            "regime_close_below_ema20": c_close_ema20,
            "regime_adx_trend": c_adx,
            "regime_minus_di_dom": c_di,
            "regime_supertrend_bearish": c_supertrend,
            "regime_futures_below_vwap": c_fut_vwap,
            "derivatives_confirmation": c_deriv_score,
            "pullback_bars_valid": c_pullback_bars,
            "pullback_depth_healthy": c_pullback_depth,
            "pullback_resistance_proximity": c_support_retest,
            "trigger_close_below_prev_low": c_trigger_low,
            "trigger_close_below_ema9": c_trigger_ema9,
            "trigger_rsi_momentum": c_rsi,
            "trigger_candle_range_normal": c_candle_range,
            "risk_r_band_valid": c_risk_band,
        }

        all_passed = regime_bearish and pullback_detected and trigger_fired and c_risk_band

        if not all_passed:
            return None

        return StrategySignal(
            signal_id=f"SIG-A-BEAR-{int(utc_now().timestamp())}",
            strategy=StrategyName.TREND_PULLBACK,
            direction=TradeDirection.BEARISH,
            option_type=OptionType.PUT,
            timestamp=utc_now(),
            spot_reference_price=spot,
            structural_stop=candidate_stop,
            r_points=r_points,
            derivatives_score=features.bear_derivatives_score,
            features_snapshot={
                "conditions": conditions,
                "pullback_high": pullback_high,
                "impulse_low": impulse_low,
                "pullback_depth": round(pullback_depth, 2),
                "r_points": r_points,
                "atr": atr,
            },
            passed=True,
        )

    def diagnose(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> list[StrategyTriggerDiagnostics]:
        """Provides condition-by-condition diagnostic breakdown of what Strategy A is waiting for."""
        spot = features.spot_price
        adx_target = overrides.adx_threshold if (overrides and overrides.adx_threshold is not None) else self.adx_threshold
        bull_deriv_target = overrides.bull_derivatives_score if (overrides and overrides.bull_derivatives_score is not None) else 2.0
        bear_deriv_target = overrides.bear_derivatives_score if (overrides and overrides.bear_derivatives_score is not None) else 2.0

        if not candles_5m or len(candles_5m) < 2:
            return [
                StrategyTriggerDiagnostics(
                    strategy=StrategyName.TREND_PULLBACK,
                    strategy_label="Trend Pullback (CALL)",
                    direction=TradeDirection.BULLISH,
                    option_type=OptionType.CALL,
                    overall_status="WAITING",
                    passed_count=0,
                    total_count=11,
                    ready_pct=0.0,
                    key_blocker="Awaiting 5-minute market candles",
                    current_spot=spot,
                ),
                StrategyTriggerDiagnostics(
                    strategy=StrategyName.TREND_PULLBACK,
                    strategy_label="Trend Pullback (PUT)",
                    direction=TradeDirection.BEARISH,
                    option_type=OptionType.PUT,
                    overall_status="WAITING",
                    passed_count=0,
                    total_count=11,
                    ready_pct=0.0,
                    key_blocker="Awaiting 5-minute market candles",
                    current_spot=spot,
                ),
            ]

        latest_5m = candles_5m[-1]
        prev_5m = candles_5m[-2]

        # --- Bullish Diagnostics ---
        c_ema_cross = features.ema20_15m > features.ema50_15m
        c_ema_slope = features.ema20_slope_15m > 0
        c_close_ema20 = spot > features.ema20_15m
        c_adx = features.adx_15m >= adx_target
        c_di = features.plus_di_15m > features.minus_di_15m
        c_supertrend = features.supertrend_direction == "BULLISH"
        c_fut_vwap = features.futures_price > features.futures_vwap
        c_deriv = features.bull_derivatives_score >= bull_deriv_target
        c_trigger_high = latest_5m.close > prev_5m.high
        c_trigger_ema9 = latest_5m.close > features.ema9_5m
        c_rsi = features.rsi_5m >= 50.0

        bull_conditions = [
            TriggerCondition(
                id="ema_trend",
                name="15m Trend Alignment (EMA20 > EMA50)",
                current_value=f"EMA20: {features.ema20_15m:.1f} | EMA50: {features.ema50_15m:.1f}",
                target_threshold="EMA20 > EMA50",
                status="PASSED" if c_ema_cross else "PENDING",
                gap_description="Passed (Bullish trend)" if c_ema_cross else f"EMA20 is {abs(features.ema20_15m - features.ema50_15m):.1f} pts below EMA50",
            ),
            TriggerCondition(
                id="ema_slope",
                name="15m EMA20 Slope",
                current_value=f"{features.ema20_slope_15m:+.2f}",
                target_threshold="> 0.00",
                status="PASSED" if c_ema_slope else "PENDING",
                gap_description="Passed (Rising slope)" if c_ema_slope else "Slope is negative or flat",
            ),
            TriggerCondition(
                id="spot_above_ema20",
                name="Spot above 15m EMA20",
                current_value=f"Spot: ₹{spot:.1f} | EMA20: {features.ema20_15m:.1f}",
                target_threshold=f"> {features.ema20_15m:.1f}",
                status="PASSED" if c_close_ema20 else "PENDING",
                gap_description="Passed" if c_close_ema20 else f"Spot is {abs(features.ema20_15m - spot):.1f} pts below 15m EMA20",
            ),
            TriggerCondition(
                id="adx_trend",
                name="15m ADX Trend Strength",
                current_value=f"{features.adx_15m:.1f}",
                target_threshold=f">= {adx_target:.1f}",
                unit="pts",
                status="PASSED" if c_adx else "PENDING",
                gap_description="Passed (Strong trend)" if c_adx else f"Need +{max(0.0, adx_target - features.adx_15m):.1f} pts ADX to qualify",
            ),
            TriggerCondition(
                id="di_dominance",
                name="Directional Dominance (+DI > -DI)",
                current_value=f"+DI: {features.plus_di_15m:.1f} | -DI: {features.minus_di_15m:.1f}",
                target_threshold="+DI > -DI",
                status="PASSED" if c_di else "PENDING",
                gap_description="Passed (+DI dominating)" if c_di else f"-DI leads by {(features.minus_di_15m - features.plus_di_15m):.1f} pts",
            ),
            TriggerCondition(
                id="supertrend",
                name="15m Supertrend Direction",
                current_value=features.supertrend_direction,
                target_threshold="BULLISH",
                status="PASSED" if c_supertrend else "PENDING",
                gap_description="Passed (Bullish)" if c_supertrend else f"Supertrend is currently {features.supertrend_direction}",
            ),
            TriggerCondition(
                id="futures_vwap",
                name="Futures vs Session VWAP",
                current_value=f"Fut: ₹{features.futures_price:.1f} | VWAP: {features.futures_vwap:.1f}",
                target_threshold=f"> {features.futures_vwap:.1f}",
                status="PASSED" if c_fut_vwap else "PENDING",
                gap_description="Passed (Above VWAP)" if c_fut_vwap else f"{(features.futures_vwap - features.futures_price):.1f} pts below VWAP",
            ),
            TriggerCondition(
                id="derivatives_flow",
                name="Derivatives Flow Bull Score",
                current_value=f"+{features.bull_derivatives_score:.1f} / 5.0",
                target_threshold=f">= +{bull_deriv_target:.1f}",
                status="PASSED" if c_deriv else "PENDING",
                gap_description="Passed (Bull flow confirmed)" if c_deriv else f"Score is +{features.bull_derivatives_score:.1f}, need +{max(0.0, bull_deriv_target - features.bull_derivatives_score):.1f} more",
            ),
            TriggerCondition(
                id="trigger_candle",
                name="5m Close > Previous Bar High",
                current_value=f"Close: ₹{latest_5m.close:.2f} | Prev High: ₹{prev_5m.high:.2f}",
                target_threshold=f"> ₹{prev_5m.high:.2f}",
                status="PASSED" if c_trigger_high else "PENDING",
                gap_description="Passed (Breakout of prior candle)" if c_trigger_high else f"Waiting for 5m close above ₹{prev_5m.high:.2f} (Gap: {max(0.0, prev_5m.high - latest_5m.close):.2f} pts)",
            ),
            TriggerCondition(
                id="trigger_ema9",
                name="5m Close > 5m EMA9",
                current_value=f"Close: ₹{latest_5m.close:.1f} | EMA9: {features.ema9_5m:.1f}",
                target_threshold=f"> {features.ema9_5m:.1f}",
                status="PASSED" if c_trigger_ema9 else "PENDING",
                gap_description="Passed" if c_trigger_ema9 else f"Need {max(0.0, features.ema9_5m - latest_5m.close):.1f} pts rise to reclaim 5m EMA9",
            ),
            TriggerCondition(
                id="rsi_momentum",
                name="5m RSI Momentum",
                current_value=f"{features.rsi_5m:.1f}",
                target_threshold=">= 50.0",
                status="PASSED" if c_rsi else "PENDING",
                gap_description="Passed" if c_rsi else f"RSI is {features.rsi_5m:.1f} (need >= 50.0)",
            ),
        ]

        bull_passed = sum(1 for c in bull_conditions if c.status == "PASSED")
        bull_total = len(bull_conditions)
        bull_pct = round((bull_passed / bull_total) * 100, 1)

        bull_blocker = "All conditions satisfied — ready to trigger entry"
        for cond in bull_conditions:
            if cond.status == "PENDING":
                bull_blocker = cond.gap_description
                break

        bull_diag = StrategyTriggerDiagnostics(
            strategy=StrategyName.TREND_PULLBACK,
            strategy_label="Trend Pullback (CALL)",
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            overall_status="READY_TO_TRIGGER" if bull_passed == bull_total else "WAITING",
            passed_count=bull_passed,
            total_count=bull_total,
            ready_pct=bull_pct,
            key_blocker=bull_blocker,
            target_entry_level=round(prev_5m.high + 0.05, 2),
            current_spot=spot,
            distance_pts=max(0.0, round(prev_5m.high - spot, 2)),
            conditions=bull_conditions,
        )

        # --- Bearish Diagnostics ---
        c_ema_cross_bear = features.ema20_15m < features.ema50_15m
        c_ema_slope_bear = features.ema20_slope_15m < 0
        c_close_ema20_bear = spot < features.ema20_15m
        c_adx_bear = features.adx_15m >= adx_target
        c_di_bear = features.minus_di_15m > features.plus_di_15m
        c_supertrend_bear = features.supertrend_direction == "BEARISH"
        c_fut_vwap_bear = features.futures_price < features.futures_vwap
        c_deriv_bear = features.bear_derivatives_score >= bear_deriv_target
        c_trigger_low = latest_5m.close < prev_5m.low
        c_trigger_ema9_bear = latest_5m.close < features.ema9_5m
        c_rsi_bear = features.rsi_5m <= 50.0

        bear_conditions = [
            TriggerCondition(
                id="ema_trend",
                name="15m Trend Alignment (EMA20 < EMA50)",
                current_value=f"EMA20: {features.ema20_15m:.1f} | EMA50: {features.ema50_15m:.1f}",
                target_threshold="EMA20 < EMA50",
                status="PASSED" if c_ema_cross_bear else "PENDING",
                gap_description="Passed (Bearish trend)" if c_ema_cross_bear else f"EMA20 is {abs(features.ema50_15m - features.ema20_15m):.1f} pts above EMA50",
            ),
            TriggerCondition(
                id="ema_slope",
                name="15m EMA20 Slope",
                current_value=f"{features.ema20_slope_15m:+.2f}",
                target_threshold="< 0.00",
                status="PASSED" if c_ema_slope_bear else "PENDING",
                gap_description="Passed (Downward slope)" if c_ema_slope_bear else "Slope is positive or flat",
            ),
            TriggerCondition(
                id="spot_below_ema20",
                name="Spot below 15m EMA20",
                current_value=f"Spot: ₹{spot:.1f} | EMA20: {features.ema20_15m:.1f}",
                target_threshold=f"< {features.ema20_15m:.1f}",
                status="PASSED" if c_close_ema20_bear else "PENDING",
                gap_description="Passed" if c_close_ema20_bear else f"Spot is {abs(spot - features.ema20_15m):.1f} pts above 15m EMA20",
            ),
            TriggerCondition(
                id="adx_trend",
                name="15m ADX Trend Strength",
                current_value=f"{features.adx_15m:.1f}",
                target_threshold=f">= {adx_target:.1f}",
                unit="pts",
                status="PASSED" if c_adx_bear else "PENDING",
                gap_description="Passed (Strong trend)" if c_adx_bear else f"Need +{max(0.0, adx_target - features.adx_15m):.1f} pts ADX to qualify",
            ),
            TriggerCondition(
                id="di_dominance",
                name="Directional Dominance (-DI > +DI)",
                current_value=f"-DI: {features.minus_di_15m:.1f} | +DI: {features.plus_di_15m:.1f}",
                target_threshold="-DI > +DI",
                status="PASSED" if c_di_bear else "PENDING",
                gap_description="Passed (-DI dominating)" if c_di_bear else f"+DI leads by {(features.plus_di_15m - features.minus_di_15m):.1f} pts",
            ),
            TriggerCondition(
                id="supertrend",
                name="15m Supertrend Direction",
                current_value=features.supertrend_direction,
                target_threshold="BEARISH",
                status="PASSED" if c_supertrend_bear else "PENDING",
                gap_description="Passed (Bearish)" if c_supertrend_bear else f"Supertrend is currently {features.supertrend_direction}",
            ),
            TriggerCondition(
                id="futures_vwap",
                name="Futures vs Session VWAP",
                current_value=f"Fut: ₹{features.futures_price:.1f} | VWAP: {features.futures_vwap:.1f}",
                target_threshold=f"< {features.futures_vwap:.1f}",
                status="PASSED" if c_fut_vwap_bear else "PENDING",
                gap_description="Passed (Below VWAP)" if c_fut_vwap_bear else f"{(features.futures_price - features.futures_vwap):.1f} pts above VWAP",
            ),
            TriggerCondition(
                id="derivatives_flow",
                name="Derivatives Flow Bear Score",
                current_value=f"+{features.bear_derivatives_score:.1f} / 5.0",
                target_threshold=f">= +{bear_deriv_target:.1f}",
                status="PASSED" if c_deriv_bear else "PENDING",
                gap_description="Passed (Bear flow confirmed)" if c_deriv_bear else f"Score is +{features.bear_derivatives_score:.1f}, need +{max(0.0, bear_deriv_target - features.bear_derivatives_score):.1f} more",
            ),
            TriggerCondition(
                id="trigger_candle",
                name="5m Close < Previous Bar Low",
                current_value=f"Close: ₹{latest_5m.close:.2f} | Prev Low: ₹{prev_5m.low:.2f}",
                target_threshold=f"< ₹{prev_5m.low:.2f}",
                status="PASSED" if c_trigger_low else "PENDING",
                gap_description="Passed (Breakdown of prior candle)" if c_trigger_low else f"Waiting for 5m close below ₹{prev_5m.low:.2f} (Gap: {max(0.0, latest_5m.close - prev_5m.low):.2f} pts)",
            ),
            TriggerCondition(
                id="trigger_ema9",
                name="5m Close < 5m EMA9",
                current_value=f"Close: ₹{latest_5m.close:.1f} | EMA9: {features.ema9_5m:.1f}",
                target_threshold=f"< {features.ema9_5m:.1f}",
                status="PASSED" if c_trigger_ema9_bear else "PENDING",
                gap_description="Passed" if c_trigger_ema9_bear else f"Need {max(0.0, latest_5m.close - features.ema9_5m):.1f} pts drop below 5m EMA9",
            ),
            TriggerCondition(
                id="rsi_momentum",
                name="5m RSI Momentum",
                current_value=f"{features.rsi_5m:.1f}",
                target_threshold="<= 50.0",
                status="PASSED" if c_rsi_bear else "PENDING",
                gap_description="Passed" if c_rsi_bear else f"RSI is {features.rsi_5m:.1f} (need <= 50.0)",
            ),
        ]

        bear_passed = sum(1 for c in bear_conditions if c.status == "PASSED")
        bear_total = len(bear_conditions)
        bear_pct = round((bear_passed / bear_total) * 100, 1)

        bear_blocker = "All conditions satisfied — ready to trigger entry"
        for cond in bear_conditions:
            if cond.status == "PENDING":
                bear_blocker = cond.gap_description
                break

        bear_diag = StrategyTriggerDiagnostics(
            strategy=StrategyName.TREND_PULLBACK,
            strategy_label="Trend Pullback (PUT)",
            direction=TradeDirection.BEARISH,
            option_type=OptionType.PUT,
            overall_status="READY_TO_TRIGGER" if bear_passed == bear_total else "WAITING",
            passed_count=bear_passed,
            total_count=bear_total,
            ready_pct=bear_pct,
            key_blocker=bear_blocker,
            target_entry_level=round(prev_5m.low - 0.05, 2),
            current_spot=spot,
            distance_pts=max(0.0, round(spot - prev_5m.low, 2)),
            conditions=bear_conditions,
        )

        return [bull_diag, bear_diag]

