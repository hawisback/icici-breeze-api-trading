"""Unit and integration tests for NIFTY Intraday Options Auto-Trading strategies.
Validates Strategy A, Strategy B, Contract Selector (under max premium cap), and Position Manager.
"""

from datetime import datetime, timezone
import pytest

from libs.contracts.models import Candle
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingConfig,
    AutoTradingMode,
    MarketFeatures,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    TradeDirection,
    TradeLifecycleState,
    utc_now,
)
from services.strategy.position_manager import PositionManager
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy


def create_sample_candles(count: int = 30, base_price: float = 23200.0, trend_step: float = 2.0) -> list[Candle]:
    """Generates synthetic candles for testing indicators and strategies."""
    candles = []
    now = utc_now()
    for i in range(count):
        ts = datetime.fromtimestamp(now.timestamp() - ((count - i) * 300), tz=timezone.utc)
        price = base_price + (i * trend_step)
        candles.append(
            Candle(
                instrument_id="INST-NIFTY-INDEX",
                interval="5m",
                start_time=ts,
                end_time=datetime.fromtimestamp(ts.timestamp() + 300, tz=timezone.utc),
                open=price - 1.0,
                high=price + 5.0,
                low=price - 4.0,
                close=price + 2.0,
                volume=100000 + (i * 2000),
            )
        )
    return candles


def test_feature_engine_calculations():
    candles = create_sample_candles(count=25, base_price=23000.0, trend_step=5.0)
    closes = [c.close for c in candles]

    ema9 = FeatureEngine.calculate_ema(closes, 9)
    ema20 = FeatureEngine.calculate_ema(closes, 20)
    assert ema9 > ema20  # In an uptrend, EMA9 should be above EMA20

    rsi = FeatureEngine.calculate_rsi(closes, 14)
    assert 50.0 < rsi <= 100.0  # Uptrend RSI should be bullish

    atr = FeatureEngine.calculate_atr(candles, 14)
    assert atr > 0.0

    adx, plus_di, minus_di = FeatureEngine.calculate_adx(candles, 14)
    assert adx > 0.0
    assert plus_di > minus_di  # Bullish uptrend

    st = FeatureEngine.calculate_supertrend(candles, 10, 3.0)
    assert st in ("BULLISH", "BEARISH")


def test_contract_selector_max_premium_cap():
    """Validates contract selection under maximum option premium cap (e.g. ₹70)."""
    cfg = OptionSelectionConfig(
        max_option_premium=70.0,
        min_option_premium=15.0,
        max_otm_strikes=4,
        min_open_interest=10000,
        prefer_premium_closest_to_cap=True,
    )
    selector = ContractSelector(config=cfg)

    # Mock option chain with strikes around 23200 spot
    # Strike 23150 CE: ask 125.0 (exceeds 70 cap)
    # Strike 23200 CE (ATM): ask 85.0 (exceeds 70 cap)
    # Strike 23250 CE (+1 OTM): ask 62.0 (under 70 cap, closest to cap!)
    # Strike 23300 CE (+2 OTM): ask 44.0 (under 70 cap, but farther from cap)
    # Strike 23350 CE (+3 OTM): ask 28.0 (under 70 cap)
    # Strike 23400 CE (+4 OTM): ask 12.0 (below 15 min floor)
    option_chain = {
        "underlying": "NIFTY",
        "atm_strike": 23200,
        "expiry": "2026-09-22",
        "strikes": [
            {
                "strike": 23150,
                "call": {"instrument_id": "CE-23150", "ask": 125.0, "bid": 124.0, "open_interest": 45000, "volume": 12000},
            },
            {
                "strike": 23200,
                "call": {"instrument_id": "CE-23200", "ask": 85.0, "bid": 84.0, "open_interest": 60000, "volume": 18000},
            },
            {
                "strike": 23250,
                "call": {"instrument_id": "CE-23250", "ask": 62.0, "bid": 61.5, "open_interest": 75000, "volume": 25000},
            },
            {
                "strike": 23300,
                "call": {"instrument_id": "CE-23300", "ask": 44.0, "bid": 43.5, "open_interest": 55000, "volume": 15000},
            },
            {
                "strike": 23350,
                "call": {"instrument_id": "CE-23350", "ask": 28.0, "bid": 27.5, "open_interest": 32000, "volume": 8000},
            },
            {
                "strike": 23400,
                "call": {"instrument_id": "CE-23400", "ask": 12.0, "bid": 11.5, "open_interest": 20000, "volume": 4000},
            },
        ],
    }

    selected, inspected, rejection_reason = selector.select_contract(
        direction=TradeDirection.BULLISH,
        spot_price=23200.0,
        option_chain=option_chain,
    )

    assert selected is not None
    assert rejection_reason is None
    # Must select 23250 CE with ask 62.0 (closest to cap of 70, not the 125 or 85 which exceed cap)
    assert selected.strike == 23250
    assert selected.ask_price == 62.0
    assert selected.instrument_id == "CE-23250"


def test_contract_selector_rejects_when_all_exceed_cap():
    cfg = OptionSelectionConfig(max_option_premium=50.0, min_option_premium=15.0)
    selector = ContractSelector(config=cfg)

    option_chain = {
        "underlying": "NIFTY",
        "atm_strike": 23200,
        "strikes": [
            {
                "strike": 23200,
                "call": {"instrument_id": "CE-23200", "ask": 85.0, "bid": 84.0, "open_interest": 60000},
            },
            {
                "strike": 23250,
                "call": {"instrument_id": "CE-23250", "ask": 65.0, "bid": 64.0, "open_interest": 60000},
            },
        ],
    }

    selected, inspected, rejection = selector.select_contract(
        direction=TradeDirection.BULLISH,
        spot_price=23200.0,
        option_chain=option_chain,
    )
    assert selected is None
    assert "EXCEED_PREMIUM_CAP" in rejection


def test_position_sizing_and_risk_limits():
    risk_cfg = RiskConfig(
        max_trade_capital=50000.0,
        risk_per_trade_pct_of_account=0.50,
        option_hard_stop_pct=25.0,
    )
    pm = PositionManager(risk_config=risk_cfg)

    # Premium: 60.0, lot size: 25. Capital per lot = 60 * 25 = 1500.
    # Capital lots = floor(50000 / 1500) = 33 lots.
    # Risk budget = 500,000 * 0.5% = 2500. Risk per lot = 60 * 25% * 25 = 375.
    # Risk lots = floor(2500 / 375) = 6 lots.
    # Allowed lots = min(33, 6) = 6 lots.
    lots, qty = pm.calculate_position_size(entry_premium=60.0, account_equity=500000.0, lot_size=25)
    assert lots == 6
    assert qty == 150


def test_position_manager_multi_level_trailing_stops():
    pm = PositionManager(session_config=SessionTimersConfig(force_exit_time="23:59"))

    # Create active bullish trade: Spot entry 23200, R_points = 25.0, initial stop 23175.0
    trade = ActiveTrade(
        trade_id="TRD-TEST-1",
        mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTY 23250 CE",
        contract_instrument_id="CE-23250",
        expiry="2026-09-22",
        strike=23250,
        quantity=50,
        lot_size=25,
        lots=2,
        entry_option_price=60.0,
        entry_spot_price=23200.0,
        initial_structural_stop=23175.0,
        initial_r_points=25.0,
        current_option_price=60.0,
        current_spot_price=23200.0,
        current_trailing_stop=23175.0,
        option_hard_stop_price=45.0,  # 25% hard stop
        state=TradeLifecycleState.OPEN_INITIAL_RISK,
    )

    features = MarketFeatures(
        spot_price=23226.0,  # +1.04R
        atr_5m=25.0,
        ema9_5m=23220.0,
        futures_price=23250.0,
        futures_vwap=23215.0,
        supertrend_direction="BULLISH",
        bull_derivatives_score=3.0,
    )

    # 1. At +1R: Stop moves to protected breakeven
    updated, exit_reason = pm.update_position(trade, current_option_price=69.0, features=features)
    assert exit_reason is None
    assert updated.current_r >= 1.0
    assert updated.state == TradeLifecycleState.PROTECTED_BREAKEVEN
    assert updated.current_trailing_stop == 23202.0  # entry + 2.0 cost buffer

    # 2. At +1.5R: Stop locks +0.5R
    features.spot_price = 23238.0  # +1.52R
    updated, exit_reason = pm.update_position(updated, current_option_price=74.0, features=features)
    assert exit_reason is None
    assert updated.state == TradeLifecycleState.PROFIT_LOCKED
    assert updated.current_trailing_stop >= 23212.5  # entry + 0.5 * 25

    # 3. Trailing stop must NEVER loosen
    features.spot_price = 23230.0  # dip back to 1.2R
    updated, exit_reason = pm.update_position(updated, current_option_price=71.0, features=features)
    assert updated.current_trailing_stop >= 23212.5  # Unchanged, did not loosen!


def test_position_manager_emergency_hard_stop():
    pm = PositionManager(session_config=SessionTimersConfig(force_exit_time="23:59"))
    trade = ActiveTrade(
        trade_id="TRD-TEST-SL",
        mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTY 23250 CE",
        contract_instrument_id="CE-23250",
        expiry="2026-09-22",
        strike=23250,
        quantity=50,
        lot_size=25,
        lots=2,
        entry_option_price=60.0,
        entry_spot_price=23200.0,
        initial_structural_stop=23175.0,
        initial_r_points=25.0,
        current_option_price=60.0,
        current_spot_price=23200.0,
        current_trailing_stop=23175.0,
        option_hard_stop_price=45.0,
        state=TradeLifecycleState.OPEN_INITIAL_RISK,
    )
    features = MarketFeatures(spot_price=23190.0, atr_5m=25.0)

    # Option drops to 44.0 (below 45.0 hard stop)
    updated, exit_reason = pm.update_position(trade, current_option_price=44.0, features=features)
    assert exit_reason is not None
    assert "OPTION_HARD_STOP_HIT" in exit_reason


def test_strategy_a_and_b_instantiation():
    strat_a = TrendPullbackStrategy(adx_threshold=20.0, rvol_threshold=1.20)
    strat_b = VolatilityBreakoutStrategy(rvol_threshold=1.30, adx_threshold=20.0)
    assert strat_a is not None
    assert strat_b is not None


def test_diagnose_trend_pullback():
    strat_a = TrendPullbackStrategy(adx_threshold=20.0)
    candles = create_sample_candles(count=20, base_price=23200.0, trend_step=2.0)
    features = MarketFeatures(
        spot_price=23240.0,
        atr_5m=20.0,
        ema9_5m=23235.0,
        ema20_5m=23225.0,
        ema20_15m=23220.0,
        ema50_15m=23200.0,
        ema20_slope_15m=0.8,
        adx_15m=24.5,
        plus_di_15m=28.0,
        minus_di_15m=14.0,
        rsi_5m=58.0,
        futures_price=23250.0,
        futures_vwap=23230.0,
        supertrend_direction="BULLISH",
        bull_derivatives_score=3.5,
    )

    diags = strat_a.diagnose(features, candles_5m=candles, candles_15m=candles)
    assert len(diags) == 2
    bull_diag, bear_diag = diags[0], diags[1]

    assert bull_diag.direction == TradeDirection.BULLISH
    assert bull_diag.option_type == OptionType.CALL
    assert bull_diag.total_count > 0
    assert bull_diag.ready_pct >= 0.0
    assert len(bull_diag.conditions) == bull_diag.total_count
    assert any(c.id == "adx_trend" for c in bull_diag.conditions)
    assert any(c.id == "derivatives_flow" for c in bull_diag.conditions)

    assert bear_diag.direction == TradeDirection.BEARISH
    assert bear_diag.option_type == OptionType.PUT


def test_diagnose_volatility_breakout():
    strat_b = VolatilityBreakoutStrategy(rvol_threshold=1.30)
    candles = create_sample_candles(count=15, base_price=23200.0, trend_step=0.5)
    features = MarketFeatures(
        spot_price=23208.0,
        atr_5m=18.0,
        bb_width_percentile=28.0,
        rvol_5m=1.45,
        futures_price=23215.0,
        futures_vwap=23205.0,
        bull_derivatives_score=2.5,
        bear_derivatives_score=1.0,
    )

    diags = strat_b.diagnose(features, candles_5m=candles)
    assert len(diags) == 2
    bull_diag, bear_diag = diags[0], diags[1]

    assert bull_diag.direction == TradeDirection.BULLISH
    assert bull_diag.total_count == 8
    assert len(bull_diag.conditions) == 8
    assert any(c.id == "bb_width" for c in bull_diag.conditions)
    assert any(c.id == "rvol_volume" for c in bull_diag.conditions)


def test_contract_selector_with_override_premium_cap():
    cfg = OptionSelectionConfig(max_option_premium=70.0)
    selector = ContractSelector(config=cfg)

    chain = {
        "underlying": "NIFTY",
        "atm_strike": 23200,
        "strikes": [
            {"strike": 23200, "call": {"instrument_id": "CE-23200", "ask": 125.0, "bid": 124.0, "open_interest": 20000, "volume": 5000}},
            {"strike": 23250, "call": {"instrument_id": "CE-23250", "ask": 85.0, "bid": 84.0, "open_interest": 20000, "volume": 5000}},
        ],
    }

    # Under default 70.0 cap, all strikes should fail
    contract, candidates, reason = selector.select_contract(
        direction=TradeDirection.BULLISH, spot_price=23200.0, option_chain=chain
    )
    assert contract is None
    assert "EXCEED_PREMIUM_CAP" in reason

    # With override_premium_cap=130.0, the best contract closest to cap (125.0) is selected!
    contract, candidates, reason = selector.select_contract(
        direction=TradeDirection.BULLISH, spot_price=23200.0, option_chain=chain, override_premium_cap=130.0
    )
    assert contract is not None
    assert contract.ask_price == 125.0
    assert contract.strike == 23200

