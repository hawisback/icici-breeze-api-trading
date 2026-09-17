"""STRATEGY B — Volatility Compression Breakout.
Implements Section 14 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
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


class VolatilityBreakoutStrategy:
    """Strategy B: Volatility Compression Breakout.
    
    Identifies tight consolidation ranges (low BBWidth, contracted ATR)
    and enters on explosive volume-backed directional breakouts.
    Bullish breakout buys Call; Bearish breakout buys Put.
    """

    def __init__(self, rvol_threshold: float = 1.30, adx_threshold: float = 20.0) -> None:
        self.rvol_threshold = rvol_threshold
        self.adx_threshold = adx_threshold

    def evaluate(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        futures_candles: Optional[list[Candle]] = None,
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        """Evaluates closed candles against Volatility Breakout setup rules."""
        if not candles_5m or len(candles_5m) < 8:
            return None

        # Check Bullish Breakout
        bull_signal = self._evaluate_bullish(features, candles_5m, overrides=overrides)
        if bull_signal:
            return bull_signal

        # Check Bearish Breakout
        bear_signal = self._evaluate_bearish(features, candles_5m, overrides=overrides)
        if bear_signal:
            return bear_signal

        return None

    def _evaluate_bullish(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)
        latest_5m = candles_5m[-1]

        bb_target = overrides.bb_width_percentile if (overrides and overrides.bb_width_percentile is not None) else 35.0
        rvol_target = overrides.rvol_threshold if (overrides and overrides.rvol_threshold is not None) else self.rvol_threshold
        deriv_target = overrides.bull_derivatives_score if (overrides and overrides.bull_derivatives_score is not None) else 2.0

        # 1. Compression Detection (preceding 4 to 10 bars)
        consolidation_bars = candles_5m[-7:-1]
        if len(consolidation_bars) < 4:
            return None

        comp_high = max(c.high for c in consolidation_bars)
        comp_low = min(c.low for c in consolidation_bars)
        comp_height = comp_high - comp_low

        c_bb_contracted = features.bb_width_percentile <= bb_target
        c_range_height = comp_height <= (1.85 * atr)
        c_compression = c_bb_contracted and c_range_height

        # 2. Bullish Breakout Trigger on Latest Closed 5m Bar
        breakout_level = comp_high + (0.10 * atr)
        c_breakout_close = latest_5m.close > breakout_level
        c_fut_vwap = features.futures_price > features.futures_vwap
        c_rvol = features.rvol_5m >= rvol_target

        candle_range = max(1.0, latest_5m.high - latest_5m.low)
        candle_body = abs(latest_5m.close - latest_5m.open)
        c_candle_body = (candle_body / candle_range) >= 0.55

        c_deriv_score = features.bull_derivatives_score >= deriv_target
        c_extension = (latest_5m.close - comp_high) <= (0.85 * atr)

        # 3. Structural Risk & R Calculation
        candidate_stop = round(comp_high - (0.25 * atr), 2)
        r_points = round(spot - candidate_stop, 2)
        c_risk_band = (0.35 * atr) <= r_points <= (1.35 * atr)

        conditions = {
            "compression_bb_contracted": c_bb_contracted,
            "compression_range_height_valid": c_range_height,
            "breakout_close_above_level": c_breakout_close,
            "futures_above_vwap": c_fut_vwap,
            "volume_rvol_elevated": c_rvol,
            "candle_body_ratio_strong": c_candle_body,
            "derivatives_confirmation": c_deriv_score,
            "extension_chase_controlled": c_extension,
            "risk_r_band_valid": c_risk_band,
        }

        all_passed = (
            c_compression
            and c_breakout_close
            and c_fut_vwap
            and c_rvol
            and c_candle_body
            and c_deriv_score
            and c_extension
            and c_risk_band
        )

        if not all_passed:
            return None

        return StrategySignal(
            signal_id=f"SIG-B-BULL-{int(utc_now().timestamp())}",
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            timestamp=utc_now(),
            spot_reference_price=spot,
            structural_stop=candidate_stop,
            r_points=r_points,
            derivatives_score=features.bull_derivatives_score,
            features_snapshot={
                "conditions": conditions,
                "comp_high": comp_high,
                "comp_low": comp_low,
                "comp_height": comp_height,
                "r_points": r_points,
                "atr": atr,
            },
            passed=True,
        )

    def _evaluate_bearish(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> Optional[StrategySignal]:
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)
        latest_5m = candles_5m[-1]

        bb_target = overrides.bb_width_percentile if (overrides and overrides.bb_width_percentile is not None) else 35.0
        rvol_target = overrides.rvol_threshold if (overrides and overrides.rvol_threshold is not None) else self.rvol_threshold
        deriv_target = overrides.bear_derivatives_score if (overrides and overrides.bear_derivatives_score is not None) else 2.0

        # 1. Compression Detection
        consolidation_bars = candles_5m[-7:-1]
        if len(consolidation_bars) < 4:
            return None

        comp_high = max(c.high for c in consolidation_bars)
        comp_low = min(c.low for c in consolidation_bars)
        comp_height = comp_high - comp_low

        c_bb_contracted = features.bb_width_percentile <= bb_target
        c_range_height = comp_height <= (1.85 * atr)
        c_compression = c_bb_contracted and c_range_height

        # 2. Bearish Breakout Trigger
        breakout_level = comp_low - (0.10 * atr)
        c_breakout_close = latest_5m.close < breakout_level
        c_fut_vwap = features.futures_price < features.futures_vwap
        c_rvol = features.rvol_5m >= rvol_target

        candle_range = max(1.0, latest_5m.high - latest_5m.low)
        candle_body = abs(latest_5m.close - latest_5m.open)
        c_candle_body = (candle_body / candle_range) >= 0.55

        c_deriv_score = features.bear_derivatives_score >= deriv_target
        c_extension = (comp_low - latest_5m.close) <= (0.85 * atr)

        # 3. Structural Risk & R Calculation
        candidate_stop = round(comp_low + (0.25 * atr), 2)
        r_points = round(candidate_stop - spot, 2)
        c_risk_band = (0.35 * atr) <= r_points <= (1.35 * atr)

        conditions = {
            "compression_bb_contracted": c_bb_contracted,
            "compression_range_height_valid": c_range_height,
            "breakout_close_below_level": c_breakout_close,
            "futures_below_vwap": c_fut_vwap,
            "volume_rvol_elevated": c_rvol,
            "candle_body_ratio_strong": c_candle_body,
            "derivatives_confirmation": c_deriv_score,
            "extension_chase_controlled": c_extension,
            "risk_r_band_valid": c_risk_band,
        }

        all_passed = (
            c_compression
            and c_breakout_close
            and c_fut_vwap
            and c_rvol
            and c_candle_body
            and c_deriv_score
            and c_extension
            and c_risk_band
        )

        if not all_passed:
            return None

        return StrategySignal(
            signal_id=f"SIG-B-BEAR-{int(utc_now().timestamp())}",
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            direction=TradeDirection.BEARISH,
            option_type=OptionType.PUT,
            timestamp=utc_now(),
            spot_reference_price=spot,
            structural_stop=candidate_stop,
            r_points=r_points,
            derivatives_score=features.bear_derivatives_score,
            features_snapshot={
                "conditions": conditions,
                "comp_high": comp_high,
                "comp_low": comp_low,
                "comp_height": comp_height,
                "r_points": r_points,
                "atr": atr,
            },
            passed=True,
        )

    def diagnose(
        self,
        features: MarketFeatures,
        candles_5m: list[Candle],
        overrides: Optional[ThresholdOverrides] = None,
    ) -> list[StrategyTriggerDiagnostics]:
        """Provides condition-by-condition diagnostic breakdown of what Strategy B is waiting for."""
        spot = features.spot_price
        atr = max(10.0, features.atr_5m)
        bb_target = overrides.bb_width_percentile if (overrides and overrides.bb_width_percentile is not None) else 35.0
        rvol_target = overrides.rvol_threshold if (overrides and overrides.rvol_threshold is not None) else self.rvol_threshold
        bull_deriv_target = overrides.bull_derivatives_score if (overrides and overrides.bull_derivatives_score is not None) else 2.0
        bear_deriv_target = overrides.bear_derivatives_score if (overrides and overrides.bear_derivatives_score is not None) else 2.0

        if not candles_5m or len(candles_5m) < 8:
            return [
                StrategyTriggerDiagnostics(
                    strategy=StrategyName.VOLATILITY_BREAKOUT,
                    strategy_label="Volatility Breakout (CALL)",
                    direction=TradeDirection.BULLISH,
                    option_type=OptionType.CALL,
                    overall_status="WAITING",
                    passed_count=0,
                    total_count=8,
                    ready_pct=0.0,
                    key_blocker="Awaiting sufficient 5m candles (min 8 bars)",
                    current_spot=spot,
                ),
                StrategyTriggerDiagnostics(
                    strategy=StrategyName.VOLATILITY_BREAKOUT,
                    strategy_label="Volatility Breakout (PUT)",
                    direction=TradeDirection.BEARISH,
                    option_type=OptionType.PUT,
                    overall_status="WAITING",
                    passed_count=0,
                    total_count=8,
                    ready_pct=0.0,
                    key_blocker="Awaiting sufficient 5m candles (min 8 bars)",
                    current_spot=spot,
                ),
            ]

        latest_5m = candles_5m[-1]
        consolidation_bars = candles_5m[-7:-1]
        comp_high = max(c.high for c in consolidation_bars)
        comp_low = min(c.low for c in consolidation_bars)
        comp_height = comp_high - comp_low

        c_bb_contracted = features.bb_width_percentile <= bb_target
        c_range_height = comp_height <= (1.85 * atr)

        candle_range = max(1.0, latest_5m.high - latest_5m.low)
        candle_body = abs(latest_5m.close - latest_5m.open)
        c_candle_body = (candle_body / candle_range) >= 0.55
        c_rvol = features.rvol_5m >= rvol_target

        # --- Bullish Diagnostics ---
        bull_breakout_lvl = round(comp_high + (0.10 * atr), 2)
        c_bull_breakout = latest_5m.close > bull_breakout_lvl
        c_bull_vwap = features.futures_price > features.futures_vwap
        c_bull_deriv = features.bull_derivatives_score >= bull_deriv_target
        c_bull_ext = (latest_5m.close - comp_high) <= (0.85 * atr)

        bull_conditions = [
            TriggerCondition(
                id="bb_width",
                name="Bollinger Band Compression",
                current_value=f"{features.bb_width_percentile:.1f}%",
                target_threshold=f"<= {bb_target:.1f}%",
                unit="%",
                status="PASSED" if c_bb_contracted else "PENDING",
                gap_description="Passed (Bands squeezed)" if c_bb_contracted else f"BB width at {features.bb_width_percentile:.1f}% (need squeeze <= {bb_target:.1f}%)",
            ),
            TriggerCondition(
                id="range_height",
                name="Consolidation Range Height",
                current_value=f"{comp_height:.1f} pts",
                target_threshold=f"<= {(1.85 * atr):.1f} pts (1.85 ATR)",
                unit="pts",
                status="PASSED" if c_range_height else "PENDING",
                gap_description="Passed (Range tight)" if c_range_height else f"Range is {comp_height:.1f} pts (exceeds max {(1.85 * atr):.1f})",
            ),
            TriggerCondition(
                id="breakout_level",
                name="5m Close > Consolidation High (+0.1 ATR)",
                current_value=f"Close: ₹{latest_5m.close:.2f} | Target: ₹{bull_breakout_lvl:.2f}",
                target_threshold=f"> ₹{bull_breakout_lvl:.2f}",
                status="PASSED" if c_bull_breakout else "PENDING",
                gap_description="Passed (Breakout confirmed)" if c_bull_breakout else f"Needs 5m close above ₹{bull_breakout_lvl:.2f} (Gap: {max(0.0, bull_breakout_lvl - latest_5m.close):.2f} pts)",
            ),
            TriggerCondition(
                id="futures_vwap",
                name="Futures vs VWAP",
                current_value=f"Fut: ₹{features.futures_price:.1f} | VWAP: {features.futures_vwap:.1f}",
                target_threshold=f"> {features.futures_vwap:.1f}",
                status="PASSED" if c_bull_vwap else "PENDING",
                gap_description="Passed (Above VWAP)" if c_bull_vwap else f"{(features.futures_vwap - features.futures_price):.1f} pts below VWAP",
            ),
            TriggerCondition(
                id="rvol_volume",
                name="5m Relative Volume (RVOL)",
                current_value=f"{features.rvol_5m:.2f}x",
                target_threshold=f">= {rvol_target:.2f}x",
                status="PASSED" if c_rvol else "PENDING",
                gap_description="Passed (High volume breakout)" if c_rvol else f"Volume RVOL is {features.rvol_5m:.2f}x (need >= {rvol_target:.2f}x)",
            ),
            TriggerCondition(
                id="candle_body",
                name="Trigger Candle Body Ratio",
                current_value=f"{(candle_body / candle_range * 100):.1f}%",
                target_threshold=">= 55.0%",
                unit="%",
                status="PASSED" if c_candle_body else "PENDING",
                gap_description="Passed (Decisive body)" if c_candle_body else f"Body ratio {(candle_body / candle_range * 100):.1f}% (need >= 55% for conviction)",
            ),
            TriggerCondition(
                id="derivatives_flow",
                name="Derivatives Flow Confirmation",
                current_value=f"+{features.bull_derivatives_score:.1f} / 5.0",
                target_threshold=f">= +{bull_deriv_target:.1f}",
                status="PASSED" if c_bull_deriv else "PENDING",
                gap_description="Passed (Bullish flow confirmed)" if c_bull_deriv else f"Score is +{features.bull_derivatives_score:.1f}, need +{max(0.0, bull_deriv_target - features.bull_derivatives_score):.1f} more",
            ),
            TriggerCondition(
                id="extension_control",
                name="Chase Extension Limit",
                current_value=f"{max(0.0, latest_5m.close - comp_high):.1f} pts from range",
                target_threshold=f"<= {(0.85 * atr):.1f} pts (0.85 ATR)",
                status="PASSED" if c_bull_ext else "PENDING",
                gap_description="Passed (Not over-extended)" if c_bull_ext else "Price over-extended beyond 0.85 ATR (risk too wide)",
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
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            strategy_label="Volatility Breakout (CALL)",
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            overall_status="READY_TO_TRIGGER" if bull_passed == bull_total else "WAITING",
            passed_count=bull_passed,
            total_count=bull_total,
            ready_pct=bull_pct,
            key_blocker=bull_blocker,
            target_entry_level=bull_breakout_lvl,
            current_spot=spot,
            distance_pts=max(0.0, round(bull_breakout_lvl - spot, 2)),
            conditions=bull_conditions,
        )

        # --- Bearish Diagnostics ---
        bear_breakout_lvl = round(comp_low - (0.10 * atr), 2)
        c_bear_breakout = latest_5m.close < bear_breakout_lvl
        c_bear_vwap = features.futures_price < features.futures_vwap
        c_bear_deriv = features.bear_derivatives_score >= bear_deriv_target
        c_bear_ext = (comp_low - latest_5m.close) <= (0.85 * atr)

        bear_conditions = [
            TriggerCondition(
                id="bb_width",
                name="Bollinger Band Compression",
                current_value=f"{features.bb_width_percentile:.1f}%",
                target_threshold=f"<= {bb_target:.1f}%",
                unit="%",
                status="PASSED" if c_bb_contracted else "PENDING",
                gap_description="Passed (Bands squeezed)" if c_bb_contracted else f"BB width at {features.bb_width_percentile:.1f}% (need squeeze <= {bb_target:.1f}%)",
            ),
            TriggerCondition(
                id="range_height",
                name="Consolidation Range Height",
                current_value=f"{comp_height:.1f} pts",
                target_threshold=f"<= {(1.85 * atr):.1f} pts (1.85 ATR)",
                unit="pts",
                status="PASSED" if c_range_height else "PENDING",
                gap_description="Passed (Range tight)" if c_range_height else f"Range is {comp_height:.1f} pts (exceeds max {(1.85 * atr):.1f})",
            ),
            TriggerCondition(
                id="breakout_level",
                name="5m Close < Consolidation Low (-0.1 ATR)",
                current_value=f"Close: ₹{latest_5m.close:.2f} | Target: ₹{bear_breakout_lvl:.2f}",
                target_threshold=f"< ₹{bear_breakout_lvl:.2f}",
                status="PASSED" if c_bear_breakout else "PENDING",
                gap_description="Passed (Breakdown confirmed)" if c_bear_breakout else f"Needs 5m close below ₹{bear_breakout_lvl:.2f} (Gap: {max(0.0, latest_5m.close - bear_breakout_lvl):.2f} pts)",
            ),
            TriggerCondition(
                id="futures_vwap",
                name="Futures vs VWAP",
                current_value=f"Fut: ₹{features.futures_price:.1f} | VWAP: {features.futures_vwap:.1f}",
                target_threshold=f"< {features.futures_vwap:.1f}",
                status="PASSED" if c_bear_vwap else "PENDING",
                gap_description="Passed (Below VWAP)" if c_bear_vwap else f"{(features.futures_price - features.futures_vwap):.1f} pts above VWAP",
            ),
            TriggerCondition(
                id="rvol_volume",
                name="5m Relative Volume (RVOL)",
                current_value=f"{features.rvol_5m:.2f}x",
                target_threshold=f">= {rvol_target:.2f}x",
                status="PASSED" if c_rvol else "PENDING",
                gap_description="Passed (High volume breakdown)" if c_rvol else f"Volume RVOL is {features.rvol_5m:.2f}x (need >= {rvol_target:.2f}x)",
            ),
            TriggerCondition(
                id="candle_body",
                name="Trigger Candle Body Ratio",
                current_value=f"{(candle_body / candle_range * 100):.1f}%",
                target_threshold=">= 55.0%",
                unit="%",
                status="PASSED" if c_candle_body else "PENDING",
                gap_description="Passed (Decisive body)" if c_candle_body else f"Body ratio {(candle_body / candle_range * 100):.1f}% (need >= 55% for conviction)",
            ),
            TriggerCondition(
                id="derivatives_flow",
                name="Derivatives Flow Confirmation",
                current_value=f"+{features.bear_derivatives_score:.1f} / 5.0",
                target_threshold=f">= +{bear_deriv_target:.1f}",
                status="PASSED" if c_bear_deriv else "PENDING",
                gap_description="Passed (Bearish flow confirmed)" if c_bear_deriv else f"Score is +{features.bear_derivatives_score:.1f}, need +{max(0.0, bear_deriv_target - features.bear_derivatives_score):.1f} more",
            ),
            TriggerCondition(
                id="extension_control",
                name="Chase Extension Limit",
                current_value=f"{max(0.0, comp_low - latest_5m.close):.1f} pts from range",
                target_threshold=f"<= {(0.85 * atr):.1f} pts (0.85 ATR)",
                status="PASSED" if c_bear_ext else "PENDING",
                gap_description="Passed (Not over-extended)" if c_bear_ext else "Price over-extended beyond 0.85 ATR (risk too wide)",
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
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            strategy_label="Volatility Breakout (PUT)",
            direction=TradeDirection.BEARISH,
            option_type=OptionType.PUT,
            overall_status="READY_TO_TRIGGER" if bear_passed == bear_total else "WAITING",
            passed_count=bear_passed,
            total_count=bear_total,
            ready_pct=bear_pct,
            key_blocker=bear_blocker,
            target_entry_level=bear_breakout_lvl,
            current_spot=spot,
            distance_pts=max(0.0, round(spot - bear_breakout_lvl, 2)),
            conditions=bear_conditions,
        )

        return [bull_diag, bear_diag]

