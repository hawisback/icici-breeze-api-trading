"""Technical, derivatives, and expected move feature calculation engine for NIFTY Intraday Options.
Implements Sections 7, 9, 10, 11, 12 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone
from statistics import median
from typing import Any, Optional
from libs.contracts.models import Candle
from services.strategy.models import MarketFeatures, utc_now

IST = timezone(timedelta(hours=5, minutes=30))


class FeatureEngine:
    """Computes technical indicators, futures volume/OI metrics, and option chain flow scores."""

    @staticmethod
    def calculate_ema(closes: list[float], period: int) -> float:
        if not closes:
            return 0.0
        if len(closes) < period:
            return float(sum(closes) / len(closes))
        k = 2.0 / (period + 1)
        ema = float(sum(closes[:period]) / period)
        for price in closes[period:]:
            ema = (price * k) + (ema * (1.0 - k))
        return round(ema, 2)

    @staticmethod
    def calculate_ema_series(closes: list[float], period: int) -> list[float]:
        if not closes:
            return []
        if len(closes) < period:
            avg = sum(closes) / len(closes)
            return [avg] * len(closes)
        k = 2.0 / (period + 1)
        res = [float(sum(closes[:period]) / period)] * period
        curr = res[-1]
        for price in closes[period:]:
            curr = (price * k) + (curr * (1.0 - k))
            res.append(round(curr, 2))
        return res

    @staticmethod
    def calculate_rsi(closes: list[float], period: int = 14) -> float:
        if len(closes) <= period:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    @staticmethod
    def calculate_atr(candles: list[Candle], period: int = 14) -> float:
        if len(candles) < 2:
            return 25.0
        trs = []
        for i in range(1, len(candles)):
            c = candles[i]
            prev = candles[i - 1]
            tr = max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
            trs.append(tr)
        if not trs:
            return 25.0
        if len(trs) < period:
            return round(sum(trs) / len(trs), 2)
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return round(atr, 2)

    @staticmethod
    def calculate_adx(candles: list[Candle], period: int = 14) -> tuple[float, float, float]:
        """Returns (adx, plus_di, minus_di)."""
        if len(candles) <= period:
            return 22.0, 25.0, 20.0

        plus_dm_list = []
        minus_dm_list = []
        tr_list = []

        for i in range(1, len(candles)):
            c = candles[i]
            prev = candles[i - 1]

            up_move = c.high - prev.high
            down_move = prev.low - c.low

            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

            tr = max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))

            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)
            tr_list.append(tr)

        if len(tr_list) < period:
            return 22.0, 25.0, 20.0

        tr_smooth = sum(tr_list[:period])
        plus_smooth = sum(plus_dm_list[:period])
        minus_smooth = sum(minus_dm_list[:period])

        dx_list = []
        for i in range(period, len(tr_list)):
            tr_smooth = tr_smooth - (tr_smooth / period) + tr_list[i]
            plus_smooth = plus_smooth - (plus_smooth / period) + plus_dm_list[i]
            minus_smooth = minus_smooth - (minus_smooth / period) + minus_dm_list[i]

            plus_di = (100.0 * plus_smooth / max(0.001, tr_smooth))
            minus_di = (100.0 * minus_smooth / max(0.001, tr_smooth))

            di_diff = abs(plus_di - minus_di)
            di_sum = plus_di + minus_di
            dx = (100.0 * di_diff / max(0.001, di_sum))
            dx_list.append((dx, plus_di, minus_di))

        if not dx_list:
            return 22.0, 25.0, 20.0

        adx = sum(d[0] for d in dx_list[:period]) / min(period, len(dx_list))
        for item in dx_list[period:]:
            adx = (adx * (period - 1) + item[0]) / period

        last_plus_di = dx_list[-1][1]
        last_minus_di = dx_list[-1][2]
        return round(adx, 2), round(last_plus_di, 2), round(last_minus_di, 2)

    @staticmethod
    def calculate_supertrend(candles: list[Candle], period: int = 10, multiplier: float = 3.0) -> str:
        """Determines Supertrend direction ('BULLISH' or 'BEARISH')."""
        if len(candles) < period+1:
            return "NEUTRAL"
        trs = [max(c.high-c.low, abs(c.high-candles[i-1].close), abs(c.low-candles[i-1].close))
               for i, c in enumerate(candles) if i > 0]
        atr = sum(trs[:period])/period
        first = candles[period]
        upper = (first.high+first.low)/2+multiplier*atr
        lower = (first.high+first.low)/2-multiplier*atr
        bullish = first.close >= (first.high+first.low)/2
        for i in range(period+1, len(candles)):
            c, previous = candles[i], candles[i-1]
            atr = (atr*(period-1)+trs[i-1])/period
            basic_upper = (c.high+c.low)/2+multiplier*atr
            basic_lower = (c.high+c.low)/2-multiplier*atr
            new_upper = basic_upper if basic_upper < upper or previous.close > upper else upper
            new_lower = basic_lower if basic_lower > lower or previous.close < lower else lower
            bullish = c.close >= new_lower if bullish else c.close > new_upper
            upper, lower = new_upper, new_lower
        return "BULLISH" if bullish else "BEARISH"

    @staticmethod
    def calculate_bollinger_bandwidth(closes: list[float], period: int = 20, num_std: float = 2.0) -> float:
        """Returns BBWidth = (UpperBand - LowerBand) / MidBand."""
        if len(closes) < period:
            return 0.015
        sub = closes[-period:]
        mean = sum(sub) / period
        variance = sum((x - mean) ** 2 for x in sub) / period
        std = math.sqrt(variance)
        upper = mean + (num_std * std)
        lower = mean - (num_std * std)
        if mean == 0:
            return 0.015
        return (upper - lower) / mean

    @classmethod
    def calculate_bb_percentile(cls, closes: list[float], history: int = 252) -> float:
        """Empirical rank against prior widths only; ties use mid-rank.

        Require at least 20 prior observations, never invent a squeeze on warm-up.
        """
        if len(closes) < 40:
            return 100.0
        widths = [cls.calculate_bollinger_bandwidth(closes[i-20:i])
                  for i in range(max(20, len(closes)-history), len(closes))]
        current = cls.calculate_bollinger_bandwidth(closes)
        return 100 * (sum(w < current for w in widths) + .5*sum(w == current for w in widths))/len(widths)

    @staticmethod
    def breakout_oi_features(chain, price, buildup):
        """Five independent evidence factors; missing OI changes earn no point.

        Walls use the nearest strike ahead within ATM +/- five listed strikes,
        a 90th-percentile rank and above-median OI (flat OI is not a wall).
        """
        bull, bear = int(buildup == "LONG_BUILDUP"), int(buildup == "SHORT_BUILDUP")
        if not chain or chain.get("source") not in ("BREEZE", "LIVE"):
            return bull, bear, False, False
        strikes = sorted(chain.get("strikes", []), key=lambda s:s["strike"])
        if not strikes:
            return bull, bear, False, False
        atm = min(range(len(strikes)), key=lambda i:abs(strikes[i]["strike"]-price))
        near = strikes[max(0,atm-5):atm+6]
        def value(s, side, key):
            return float((s.get(side) or {}).get(key) or 0)
        below, above = [s for s in near if s["strike"] <= price], [s for s in near if s["strike"] >= price]
        bull += int(any(value(s,"put","oi_change") > 0 for s in below))
        bear += int(any(value(s,"call","oi_change") > 0 for s in above))
        bull += int(any(value(s,"call","oi_change") < 0 for s in above))
        bear += int(any(value(s,"put","oi_change") < 0 for s in below))
        balance = sum(value(s,"put","oi_change")-value(s,"call","oi_change") for s in near)
        bull += int(balance > 0)
        bear += int(balance < 0)
        velocity = sum(value(s,"put","oi_velocity")-value(s,"call","oi_velocity") for s in near)
        bull += int(velocity > 0)
        bear += int(velocity < 0)
        def wall(side, ahead):
            oi = [value(s,side,"open_interest") for s in near]
            if not ahead or len(oi) < 3 or median(oi) <= 0:
                return False
            target = value(ahead[0],side,"open_interest")
            return target > median(oi) and sum(x <= target for x in oi)/len(oi) >= .90
        return bull,bear,wall("call",above),wall("put",list(reversed(below)))

    @staticmethod
    def calculate_futures_vwap(futures_candles: list[Candle]) -> float:
        """Computes session VWAP = sum(TypicalPrice * Volume) / sum(Volume)."""
        if not futures_candles:
            return 0.0
        total_vol = 0
        total_pv = 0.0
        session = futures_candles[-1].start_time.astimezone(IST).date()
        for c in futures_candles:
            if c.start_time.astimezone(IST).date() != session:
                continue
            tp = (c.high + c.low + c.close) / 3.0
            vol = max(0, c.volume)
            total_vol += vol
            total_pv += (tp * vol)
        if total_vol == 0:
            return 0.0
        return round(total_pv / total_vol, 2)

    @staticmethod
    def calculate_rvol(futures_candles: list[Candle]) -> float:
        """Relative volume of latest 5m candle vs median volume of preceding bars."""
        if len(futures_candles) < 2:
            return 0.0
        latest = futures_candles[-1]
        slot = latest.start_time.astimezone(IST)
        volumes = [c.volume for c in futures_candles[:-1]
                   if c.start_time.astimezone(IST).date() < slot.date()
                   and c.start_time.astimezone(IST).time() == slot.time()]
        if not volumes:
            volumes = [c.volume for c in futures_candles[:-1]
                       if c.start_time.astimezone(IST).date() == slot.date()]
        base = median(volumes) if volumes else 0
        return round(latest.volume / base, 2) if base > 0 else 0.0

    @classmethod
    def compute_all_features(
        cls,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        futures_candles: Optional[list[Candle]] = None,
        option_chain: Optional[dict[str, Any]] = None,
        spot_price: float = 23217.60,
        as_of=None,
    ) -> MarketFeatures:
        """Synthesize all technical, derivatives, and contextual indicators."""
        now = as_of or utc_now()
        def completed(bars, interval):
            return sorted({c.start_time: c for c in (bars or [])
                           if c.source in ("BREEZE", "LIVE") and c.interval == interval
                           and c.end_time <= now and c.low > 0
                           and c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
                           and c.end_time - c.start_time == timedelta(minutes=int(interval[:-1]))
                           }.values(), key=lambda c: c.start_time)
        candles_5m = completed(candles_5m, "5m")
        candles_15m = completed(candles_15m, "15m")
        futures_candles = completed(futures_candles, "5m")
        closes_5m = [c.close for c in candles_5m] if candles_5m else [spot_price]
        closes_15m = [c.close for c in candles_15m] if candles_15m else [spot_price]

        # 15m Indicators
        ema9_15m = cls.calculate_ema(closes_15m, 9)
        ema20_15m = cls.calculate_ema(closes_15m, 20)
        ema50_15m = cls.calculate_ema(closes_15m, 50)
        ema20_series = cls.calculate_ema_series(closes_15m, 20)
        raw_slope = (ema20_series[-1] - ema20_series[-3]) if len(ema20_series) >= 3 else 0.0
        atr_15m = cls.calculate_atr(candles_15m, 14) if candles_15m else 0.0
        slope_norm = raw_slope / atr_15m if atr_15m > 0 else 0.0

        adx_15m, plus_di, minus_di = cls.calculate_adx(candles_15m, 14) if len(candles_15m) >= 29 else (0.0, 0.0, 0.0)
        _, plus_di_5m, minus_di_5m = cls.calculate_adx(candles_5m, 14) if len(candles_5m) >= 29 else (0.0, 0.0, 0.0)

        # 5m Indicators
        ema9_5m = cls.calculate_ema(closes_5m, 9)
        ema20_5m = cls.calculate_ema(closes_5m, 20)
        rsi_5m = cls.calculate_rsi(closes_5m, 14)
        atr_5m = cls.calculate_atr(candles_5m, 14) if candles_5m else 0.0
        supertrend = cls.calculate_supertrend(candles_5m, 10, 3.0) if candles_5m else "BULLISH"
        bb_width = cls.calculate_bollinger_bandwidth(closes_5m, 20, 2.0)
        bb_percentile = cls.calculate_bb_percentile(closes_5m)

        # Futures & VWAP
        if futures_candles and len(futures_candles) > 0:
            fut_price = futures_candles[-1].close
            fut_vwap = cls.calculate_futures_vwap(futures_candles)
            rvol = cls.calculate_rvol(futures_candles)
        else:
            fut_price = fut_vwap = rvol = 0.0

        # Futures OI Buildup
        fut_buildup = "NEUTRAL"
        if len(futures_candles) >= 2:
            previous, current = futures_candles[-2:]
            if previous.open_interest and current.open_interest:
                price_change = current.close - previous.close
                oi_change = current.open_interest - previous.open_interest
                if price_change > 0 and oi_change > 0:
                    fut_buildup = "LONG_BUILDUP"
                elif price_change < 0 and oi_change > 0:
                    fut_buildup = "SHORT_BUILDUP"
                elif price_change > 0 and oi_change < 0:
                    fut_buildup = "SHORT_COVERING"
                elif price_change < 0 and oi_change < 0:
                    fut_buildup = "LONG_UNWINDING"

        # Option Chain Derivatives Confirmation Score
        bull_score = 0.0
        bear_score = 0.0

        # Process Option Chain OI flow if available
        if option_chain and "strikes" in option_chain:
            strikes = option_chain.get("strikes", [])
            atm_strike = option_chain.get("atm_strike", spot_price)

            total_put_oi = sum(s.get("put", {}).get("open_interest", 0) for s in strikes if s.get("put"))
            total_call_oi = sum(s.get("call", {}).get("open_interest", 0) for s in strikes if s.get("call"))

            # If put OI is higher or growing near/below ATM -> support
            if total_put_oi > total_call_oi:
                bull_score += 1.0
            elif total_call_oi > total_put_oi:
                bear_score += 1.0

            # Inspect strikes immediately above ATM for call writing walls
            strikes_above = [s for s in strikes if s["strike"] > atm_strike][:3]
            call_oi_above = sum(s.get("call", {}).get("open_interest", 0) for s in strikes_above if s.get("call"))
            if call_oi_above > 500000:
                bull_score -= 1.0
                bear_score += 1.0

        b_bull, b_bear, bull_wall, bear_wall = cls.breakout_oi_features(option_chain, candles_5m[-1].close if candles_5m else spot_price, fut_buildup)

        # Baseline trend regime
        if ema20_15m > ema50_15m and spot_price > ema20_15m and slope_norm >= 0.10:
            regime = "BULLISH"
        elif ema20_15m < ema50_15m and spot_price < ema20_15m and slope_norm <= -0.10:
            regime = "BEARISH"
        else:
            regime = "NEUTRAL"

        swing_lows = [candles_5m[i].low for i in range(1, len(candles_5m)-1)
                      if candles_5m[i].low < candles_5m[i-1].low and candles_5m[i].low <= candles_5m[i+1].low]
        swing_highs = [candles_5m[i].high for i in range(1, len(candles_5m)-1)
                       if candles_5m[i].high > candles_5m[i-1].high and candles_5m[i].high >= candles_5m[i+1].high]
        ready = (len(candles_5m) >= 29 and len(candles_15m) >= 50 and len(futures_candles) >= 15
                 and candles_5m[-1].end_time == futures_candles[-1].end_time
                 and 0 <= (now - candles_5m[-1].end_time).total_seconds() < 300
                 and 0 <= (now - candles_15m[-1].end_time).total_seconds() < 900
                 and atr_5m > 0 and atr_15m > 0 and fut_vwap > 0)
        return MarketFeatures(
            breakout_data_ready=(len(candles_5m) >= 40 and len(futures_candles) >= 15
                and candles_5m[-1].end_time == futures_candles[-1].end_time
                and 0 <= (now-candles_5m[-1].end_time).total_seconds() < 300
                and atr_5m > 0 and fut_vwap > 0),
            breakout_bull_derivatives_score=b_bull,
            breakout_bear_derivatives_score=b_bear,
            bullish_oi_wall=bull_wall,
            bearish_oi_wall=bear_wall,
            timestamp=now,
            closed_5m_price=candles_5m[-1].close if candles_5m else None,
            closed_5m_time=candles_5m[-1].end_time if candles_5m else None,
            plus_di_5m=plus_di_5m,
            minus_di_5m=minus_di_5m,
            swing_low_5m=swing_lows[-1] if swing_lows else None,
            swing_high_5m=swing_highs[-1] if swing_highs else None,
            futures_atr_5m=cls.calculate_atr(futures_candles) if len(futures_candles) >= 15 else 0,
            data_ready=ready,
            data_reason="" if ready else "Missing, stale, unaligned or insufficient real spot/futures candles",
            spot_price=spot_price,
            spot_change_pct=0.0,
            ema9_15m=ema9_15m,
            ema20_15m=ema20_15m,
            ema50_15m=ema50_15m,
            ema20_slope_15m=round(raw_slope, 2),
            ema20_slope_norm_15m=round(slope_norm, 4),
            adx_15m=adx_15m,
            plus_di_15m=plus_di,
            minus_di_15m=minus_di,
            atr_15m=atr_15m,
            ema9_5m=ema9_5m,
            ema20_5m=ema20_5m,
            rsi_5m=rsi_5m,
            atr_5m=atr_5m,
            daily_atr=165.0,
            supertrend_direction=supertrend,
            bb_width_percentile=bb_percentile,
            futures_price=fut_price,
            futures_vwap=fut_vwap,
            rvol_5m=rvol,
            futures_buildup=fut_buildup,
            bull_derivatives_score=max(0.0, bull_score),
            bear_derivatives_score=max(0.0, bear_score),
            expected_daily_points=165.0,
            remaining_session_points=95.0,
            atm_straddle_price=215.0,
            trend_regime=regime,
        )
