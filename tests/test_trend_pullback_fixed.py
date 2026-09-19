from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade, AutoTradingConfig, MarketFeatures, SessionTimersConfig,
    ThresholdOverrides, TradeDirection,
)
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.position_manager import PositionManager
from services.strategy.repository import StrategyRepository
from services.strategy.contract_selector import ContractSelector
from services.strategy.simulation import SimulationEngine


def setup(bear=False):
    start = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    prices = [(106,110,102,107), (105,108,100,106), (106,119,105,117),
              (117,120,114,118), (117,118,113,114), (114,116,112,113), (114,119,113,118)]
    bars = []
    for i, (o,h,l,c) in enumerate(prices):
        if bear:
            o,h,l,c = 220-o,220-l,220-h,220-c
        bars.append(Candle(instrument_id="INDEX", interval="5m", start_time=start+timedelta(minutes=5*i),
                           end_time=start+timedelta(minutes=5*(i+1)), open=o,high=h,low=l,close=c,volume=0))
    futures = [c.model_copy(update={"instrument_id":"FUT", "open":c.open+30,"high":c.high+30,
                                   "low":c.low+30,"close":c.close+30,
                                   "volume":50 if i in (4,5,6) else 100}) for i,c in enumerate(bars)]
    macro = [bars[-1].model_copy(update={"interval":"15m", "start_time":bars[-1].end_time-timedelta(minutes=15)})]
    spot = 100.0 if bear else 120.0
    features = MarketFeatures(timestamp=bars[-1].end_time, spot_price=spot, data_ready=True, data_reason="",
                             closed_5m_price=bars[-1].close, closed_5m_time=bars[-1].end_time,
                             atr_5m=10, futures_atr_5m=10, adx_15m=25,
                             ema20_15m=120 if bear else 100, ema50_15m=130 if bear else 90,
                             ema20_slope_norm_15m=-.15 if bear else .15,
                             plus_di_15m=10 if bear else 30, minus_di_15m=30 if bear else 10,
                             ema9_5m=107 if bear else 113, ema20_5m=108 if bear else 112,
                             rsi_5m=45 if bear else 55, rvol_5m=1.25,
                             futures_price=132 if bear else 148, futures_vwap=140,
                             supertrend_direction="BEARISH" if bear else "BULLISH")
    return features, bars, macro, futures


@pytest.mark.parametrize("bear", [False, True])
def test_signal_diagnostic_parity_frozen_r_volume_and_dedup(bear):
    f,b,m,fu = setup(bear)
    strategy = TrendPullbackStrategy(breakout_confirm_polls=2)
    # Poll 1
    diag = strategy.diagnose(f,b,m,futures_candles=fu)[int(bear)]
    assert diag.phase_state == "WAIT_FOR_TRIGGER"
    assert strategy.export_state() == {"BULLISH":{}, "BEARISH":{}}
    sig1 = strategy.evaluate(f,b,m,fu)
    assert sig1 is None

    # Poll 2
    diag2 = strategy.diagnose(f,b,m,futures_candles=fu)[int(bear)]
    assert diag2.overall_status == "READY_TO_TRIGGER"
    signal = strategy.evaluate(f,b,m,fu)
    assert signal is not None
    assert signal.direction == (TradeDirection.BEARISH if bear else TradeDirection.BULLISH)
    assert signal.spot_reference_price == f.spot_price
    assert signal.features_snapshot["pullback_bars"] >= 2
    assert signal.r_points == pytest.approx(abs(f.spot_price - signal.structural_stop))
    restored = TrendPullbackStrategy(breakout_confirm_polls=2)
    restored.restore_state(strategy.export_state())
    assert restored.evaluate(f,b,m,fu) is None


def test_rvol_override_changes_decision():
    f,b,m,fu = setup()
    # Supertrend + VWAP + pullback volume = 3. RVOL provides the fourth.
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f,b,m,fu,ThresholdOverrides(min_confirmation_score=4,rvol_threshold=1.2))
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f,b,m,fu,ThresholdOverrides(min_confirmation_score=4,rvol_threshold=1.3)) is None


def test_macro_regime_relaxed_rules():
    # CALL Tests
    # 1. EMA20 > EMA50, Close > EMA20, Slope positive, ADX below 18, DI unfavorable -> PASS (support_score = 1)
    f, b, m, fu = setup(bear=False)
    f.ema20_15m = 100.0
    f.ema50_15m = 90.0
    m[-1] = m[-1].model_copy(update={"close": 118.0})
    f.ema20_slope_15m = 1.0
    f.ema20_slope_norm_15m = 0.1
    f.adx_15m = 15.0
    f.plus_di_15m = 10.0
    f.minus_di_15m = 30.0
    sig = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu)
    assert sig is not None, "CALL should pass with support_score = 1 from slope alone"
    assert sig.direction == TradeDirection.BULLISH

    # 2. EMA20 > EMA50, Close > EMA20, Slope flat/negative, DI unfavorable, ADX < 18 -> FAIL (support_score = 0)
    f.ema20_slope_15m = 0.0
    f.ema20_slope_norm_15m = 0.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu) is None, "CALL should fail when support_score = 0"

    # 3. EMA20 <= EMA50 -> FAIL regardless of support score
    f.ema20_15m = 85.0
    f.ema50_15m = 90.0
    f.adx_15m = 25.0
    f.ema20_slope_15m = 2.0
    f.plus_di_15m = 40.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu) is None, "CALL must fail when EMA20 <= EMA50"

    # 4. Close <= EMA20 -> FAIL regardless of support score
    f.ema20_15m = 120.0
    f.ema50_15m = 90.0
    m[-1] = m[-1].model_copy(update={"close": 115.0})  # Close (115) <= EMA20 (120)
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu) is None, "CALL must fail when Close <= EMA20"

    # PUT Tests
    # 1. EMA20 < EMA50, Close < EMA20, Slope negative, ADX below 18, DI unfavorable -> PASS (support_score = 1)
    f_p, b_p, m_p, fu_p = setup(bear=True)
    f_p.ema20_15m = 100.0
    f_p.ema50_15m = 110.0
    m_p[-1] = m_p[-1].model_copy(update={"close": 82.0})
    f_p.ema20_slope_15m = -1.0
    f_p.ema20_slope_norm_15m = -0.1
    f_p.adx_15m = 15.0
    f_p.minus_di_15m = 10.0
    f_p.plus_di_15m = 30.0
    sig_p = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f_p, b_p, m_p, fu_p)
    assert sig_p is not None, "PUT should pass with support_score = 1 from slope alone"
    assert sig_p.direction == TradeDirection.BEARISH

    # 2. EMA20 < EMA50, Close < EMA20, Slope flat/positive, DI unfavorable, ADX < 18 -> FAIL (support_score = 0)
    f_p.ema20_slope_15m = 0.0
    f_p.ema20_slope_norm_15m = 0.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f_p, b_p, m_p, fu_p) is None, "PUT should fail when support_score = 0"

    # 3. EMA20 >= EMA50 -> FAIL regardless of support score
    f_p.ema20_15m = 120.0
    f_p.ema50_15m = 110.0
    f_p.adx_15m = 25.0
    f_p.ema20_slope_15m = -2.0
    f_p.minus_di_15m = 40.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f_p, b_p, m_p, fu_p) is None, "PUT must fail when EMA20 >= EMA50"

    # 4. Close >= EMA20 -> FAIL regardless of support score
    f_p.ema20_15m = 80.0
    f_p.ema50_15m = 110.0
    m_p[-1] = m_p[-1].model_copy(update={"close": 85.0})  # Close (85) >= EMA20 (80)
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f_p, b_p, m_p, fu_p) is None, "PUT must fail when Close >= EMA20"


def test_missing_stale_and_synthetic_data_do_not_signal():
    f,b,m,fu = setup()
    assert TrendPullbackStrategy().evaluate(f,b,m) is None
    fu[-1] = fu[-1].model_copy(update={"source":"SIMULATED"})
    assert TrendPullbackStrategy().evaluate(f,b,m,fu) is None
    fu[-1] = fu[-1].model_copy(update={"source":"BREEZE"})
    f.timestamp += timedelta(minutes=5)
    assert TrendPullbackStrategy().evaluate(f,b,m,fu) is None


def test_trigger_not_counted_as_second_pullback_bar():
    f,b,m,fu = setup()
    b.pop(5)
    fu.pop(5)
    assert TrendPullbackStrategy().evaluate(f,b,m,fu) is None


def test_no_synthetic_futures_confirmations():
    f,b,m,fu = setup()
    result = FeatureEngine.compute_all_features(b,m,spot_price=118,as_of=f.timestamp)
    assert not result.data_ready
    assert result.futures_price == result.futures_vwap == result.rvol_5m == 0
    assert result.futures_buildup == "NEUTRAL"
    assert result.bull_derivatives_score == result.bear_derivatives_score == 0


def test_completed_resample_only():
    f,b,m,fu = setup()
    assert SimulationEngine.resample_to_15m(b[:2]) == []
    result = SimulationEngine.resample_to_15m(b[:3])
    assert len(result) == 1
    assert result[0].end_time-result[0].start_time == timedelta(minutes=15)


def trade():
    return ActiveTrade(trade_id="test", mode="PAPER", strategy="TREND_PULLBACK", direction="BULLISH",
                       option_type="CALL",contract_symbol="TEST",contract_instrument_id="TEST",expiry="2026-09-24",
                       strike=100,quantity=50,lot_size=50,lots=1,entry_option_price=60,entry_spot_price=118,
                       entry_time=datetime(2026,9,17,4,35,tzinfo=timezone.utc),initial_structural_stop=110.5,
                       initial_r_points=7.5,pullback_swing_low=112,current_option_price=60,current_spot_price=118,
                       current_trailing_stop=110.5,option_hard_stop_price=45)


def test_invalidation_waits_for_close_and_retains_intrabar_hard_stop():
    pm = PositionManager(session_config=SessionTimersConfig(force_exit_time="23:59"))
    t = trade()
    f = MarketFeatures(spot_price=111,closed_5m_price=118,closed_5m_time=t.entry_time)
    assert pm.update_position(t,60,f)[1] is None
    f.closed_5m_price = 111
    f.closed_5m_time += timedelta(minutes=5)
    f.timestamp = f.closed_5m_time
    assert "THESIS_INVALIDATION" in pm.update_position(t,60,f)[1]
    assert "OPTION_HARD_STOP" in pm.update_position(trade(),40,MarketFeatures(spot_price=118))[1]


@pytest.mark.asyncio
async def test_repository_round_trip_and_default_migration(tmp_path):
    repo = StrategyRepository(tmp_path/"strategy.db")
    await repo.initialize()
    t = trade()
    t.highest_close_since_entry = 125
    t.last_managed_bar = t.entry_time
    await repo.save_trade(t)
    restored = (await repo.get_active_trades())[0]
    assert restored.model_dump() == t.model_dump()
    await repo.save_runtime({"BULLISH":{"consumed":"test"}})
    assert (await repo.get_runtime())["BULLISH"]["consumed"] == "test"
    cfg = AutoTradingConfig(strategy_a_revision=1)
    cfg.tunables.rvol_threshold = 1.3
    cfg.session.no_new_trade_before = "09:30"
    await repo.save_auto_config(cfg)
    new = await repo.get_auto_config()
    assert new.tunables.rvol_threshold == 1.2
    assert new.session.no_new_trade_before == "09:20"


def test_contract_uses_metadata_and_rejects_missing_lot():
    chain = {"expiry":"2026-09-24", "strikes":[{"strike":100,"call":{"instrument_id":"TEST-CE", "ask":60,"bid":59.5,"volume":1000,"open_interest":20000,"lot_size":75}}]}
    selected,_,_ = ContractSelector().select_contract(TradeDirection.BULLISH,100,chain)
    assert selected.lot_size == 75
    del chain["strikes"][0]["call"]["lot_size"]
    assert ContractSelector().select_contract(TradeDirection.BULLISH,100,chain)[0] is None


@pytest.mark.parametrize("count,accepted", [(1,True),(2,True),(9,True),(10,False)])
def test_pullback_age_boundaries(count, accepted):
    f,b,m,fu = setup()
    prehistory = b[0].model_copy(update={
        "start_time": b[0].start_time - timedelta(minutes=5),
        "end_time": b[0].end_time - timedelta(minutes=5),
    })
    raw = [prehistory] + b[:4] + [b[5]]*count
    start = b[0].start_time
    bars = [c.model_copy(update={"start_time":start+timedelta(minutes=5*i),
                                 "end_time":start+timedelta(minutes=5*(i+1))}) for i,c in enumerate(raw)]
    futures = [c.model_copy(update={"instrument_id":"FUT", "volume":50}) for c in bars]
    f.timestamp = bars[-1].end_time

    f.spot_price = bars[-1].high + 1.0
    m = [m[0].model_copy(update={"end_time":f.timestamp, "start_time":f.timestamp-timedelta(minutes=15)})]
    strategy = TrendPullbackStrategy(breakout_confirm_polls=1)
    cutoff = bars[4].end_time.isoformat()
    strategy.state["BULLISH"].update({
        "session": bars[-1].start_time.date().isoformat(),
        "regime_qualified_since": cutoff,
        "setup_cutoff": cutoff,
        "after": cutoff,
    })
    signal = strategy.evaluate(f,bars,m,futures)
    assert bool(signal) is accepted
    if count == 10:
        assert "impulse" not in strategy.state["BULLISH"]


def test_no_atr_floor():
    f,b,m,fu = setup()
    def scale(c):
        return c.model_copy(update={key:getattr(c,key)/2 for key in ("open","high","low","close")})
    b,m,fu = list(map(scale,b)),list(map(scale,m)),list(map(scale,fu))
    for field in ("atr_5m","futures_atr_5m","ema9_5m","ema20_5m","ema20_15m","ema50_15m","futures_price","futures_vwap","spot_price"):
        setattr(f,field,getattr(f,field)/2)
    f.spot_price = b[-1].high + 0.5
    sig = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f,b,m,fu)
    assert sig is not None
    assert sig.features_snapshot["atr"] == 5


def test_vwap_resets_and_buildup_uses_actual_oi():
    f,b,m,fu = setup()
    prior = fu[0].model_copy(update={"start_time":fu[0].start_time-timedelta(days=1),
                                    "end_time":fu[0].end_time-timedelta(days=1), "high":1000,"low":1000,"close":1000,"volume":1000000})
    assert FeatureEngine.calculate_futures_vwap([prior]+fu) == FeatureEngine.calculate_futures_vwap(fu)
    result = FeatureEngine.compute_all_features(b,m,fu,spot_price=118,as_of=f.timestamp)
    assert result.futures_buildup == "NEUTRAL"
    assert result.bull_derivatives_score == result.bear_derivatives_score == 0
    fu[-2] = fu[-2].model_copy(update={"open_interest":1000})
    fu[-1] = fu[-1].model_copy(update={"open_interest":900})
    result = FeatureEngine.compute_all_features(b,m,fu,spot_price=118,as_of=f.timestamp)
    assert result.futures_buildup == "SHORT_COVERING"


def test_quote_provenance_cumulative_volume_and_closed_bar_oi():
    from libs.contracts.models import Quote
    from services.market_data.candle_builder import CandleBuilder
    builder = CandleBuilder(5)
    start = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)
    def quote(minutes, volume, oi, source="BREEZE"):
        return Quote(instrument_id="FUT", symbol="FUT", last_price=100,
                     timestamp=start+timedelta(minutes=minutes), volume=volume,
                     open_interest=oi, source=source)
    assert builder.process_quote(quote(0, 1000, 10)) is None
    assert builder.process_quote(quote(1, 1100, 11)) is None
    candle = builder.process_quote(quote(5, 1200, 99))
    assert candle.source == "BREEZE"
    assert candle.volume == 100
    assert candle.open_interest == 11
    assert builder.process_quote(quote(6, 1300, 100, "SIMULATED")) is None
    candle = builder.process_quote(quote(10, 1400, 101, "SIMULATED"))
    assert candle.source == "SIMULATED"


def test_reset_before_first_evaluation_preserves_cutoff():
    f,b,m,fu = setup()
    strategy = TrendPullbackStrategy(breakout_confirm_polls=2)
    strategy.reset(f.timestamp)
    assert strategy.evaluate(f,b,m,fu) is None
    assert strategy.state["BULLISH"]["after"] == f.timestamp.isoformat()


def test_regime_cutoff_is_created_preserved_and_recreated_after_invalidation():
    f, bars, macro, futures = setup()
    strategy = TrendPullbackStrategy(breakout_confirm_polls=99)

    # First qualifying evaluation creates the directional setup cutoff.
    assert strategy.evaluate(f, bars, macro, futures) is None
    first_cutoff = strategy.state["BULLISH"]["setup_cutoff"]
    assert first_cutoff == macro[-1].start_time.isoformat()
    assert strategy.state["BULLISH"]["regime_qualified_since"] == first_cutoff
    first_diag = strategy.diagnose(f, bars, macro, futures_candles=futures)[0]
    assert first_diag.phase_summary["setup_cutoff_event"] == "PRESERVED"
    assert first_diag.phase_summary["setup_direction"] == "BULLISH"
    assert first_diag.phase_summary["macro_regime_qualified"] is True

    # Repeated polling of the same qualifying regime preserves it.
    for _ in range(3):
        assert strategy.evaluate(f, bars, macro, futures) is None
        assert strategy.state["BULLISH"]["setup_cutoff"] == first_cutoff

    # A failed bullish regime clears only the bullish setup cutoff.
    f.ema20_15m = 130.0
    f.ema50_15m = 140.0
    macro[0] = macro[0].model_copy(update={"close": 118.0})
    assert strategy.evaluate(f, bars, macro, futures) is None
    assert strategy.state["BULLISH"].get("setup_cutoff") is None
    assert strategy.state["BULLISH"].get("regime_qualified_since") is None
    invalidated_diag = strategy.diagnose(f, bars, macro, futures_candles=futures)[0]
    assert invalidated_diag.phase_summary["setup_cutoff_event"] == "INVALIDATED"

    # Qualification after invalidation creates a fresh cutoff.
    f.ema20_15m = 100.0
    f.ema50_15m = 90.0
    f.timestamp = bars[-1].end_time + timedelta(minutes=5)
    next_bar = bars[-1].model_copy(update={
        "start_time": bars[-1].start_time + timedelta(minutes=5),
        "end_time": bars[-1].end_time + timedelta(minutes=5),
    })
    next_future = next_bar.model_copy(update={"instrument_id": "FUT"})
    bars = bars + [next_bar]
    futures = futures + [next_future]
    macro = [macro[0].model_copy(update={
        "start_time": next_bar.end_time - timedelta(minutes=15),
        "end_time": next_bar.end_time,
        "close": 118.0,
    })]
    assert strategy.evaluate(f, bars, macro, futures) is None
    assert strategy.state["BULLISH"]["setup_cutoff"] == macro[0].start_time.isoformat()
    assert strategy.state["BULLISH"]["setup_cutoff"] != first_cutoff


@pytest.mark.asyncio
async def test_live_fill_price_and_pending_exit_are_reconciled():
    from types import SimpleNamespace
    from services.strategy.service import StrategyService
    from services.strategy.models import AutoTradingMode, TradeLifecycleState, utc_now
    t = trade()
    t.mode = AutoTradingMode.LIVE
    t.entry_order_id = "entry"
    repo = Mock(save_trade=AsyncMock(), save_runtime=AsyncMock(), save_decision_log=AsyncMock())
    filled = SimpleNamespace(status=SimpleNamespace(value="FILLED"), filled_quantity=50, average_price=64)
    oms = Mock(get_order=AsyncMock(return_value=filled), create_order_intent=AsyncMock(return_value=SimpleNamespace(order_id="exit")))
    market = Mock(get_latest_quote=Mock(return_value=SimpleNamespace(best_bid=58, timestamp=utc_now(), source="BREEZE")))
    svc = StrategyService(oms, repository=repo, market_data_service=market, event_bus=Mock(publish=AsyncMock()))
    svc.position_manager = Mock(update_position=Mock(return_value=(t,"TEST_EXIT")))
    f = MarketFeatures(spot_price=120)
    await svc._evaluate_active_trade(t,f)
    assert t.entry_option_price == 64
    assert t.option_hard_stop_price == 48
    assert t.entry_spot_price == 118
    assert t.state == TradeLifecycleState.EXIT_PENDING
    intent = oms.create_order_intent.call_args.args[0]
    assert intent.price == 58
    assert t.exit_time is None
    oms.get_order.side_effect = [filled,SimpleNamespace(status=SimpleNamespace(value="FILLED"),filled_quantity=50,average_price=57.5)]
    await svc._evaluate_active_trade(t,f)
    assert t.state == TradeLifecycleState.CLOSED
    assert t.exit_option_price == 57.5


@pytest.mark.asyncio
async def test_management_rejects_simulated_quote_and_refreshes_exact_contract():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from libs.contracts.models import utc_now
    from services.strategy.service import StrategyService
    t = trade()
    market = Mock(get_latest_quote=Mock(return_value=SimpleNamespace(
        best_bid=100, timestamp=utc_now(), source="SIMULATED")))
    svc = StrategyService(Mock(), market_data_service=market)
    assert await svc._executable_bid(t) is None
    chain = Mock(get_chain=AsyncMock(return_value={"source":"BREEZE", "strikes":[
        {"call":{"instrument_id":t.contract_instrument_id,"bid":52.5}}]}))
    svc.chain_svc = chain
    assert await svc._executable_bid(t) == 52.5
    chain.get_chain.assert_awaited_once_with(underlying="NIFTY", expiry=t.expiry)
    chain.get_chain.return_value["source"] = "SIMULATED"
    assert await svc._executable_bid(t) is None


# ==============================================================================
# ITERATION 1 — Breakout Tests
# ==============================================================================

def test_call_breakout_live_price_and_polling():
    f, b, m, fu = setup(bear=False)
    # trigger price is b[-1].high + 0.02 * atr = 119 + 0.2 = 119.2
    strategy = TrendPullbackStrategy(breakout_confirm_polls=2)

    # 1. CALL live price below trigger -> no signal
    f.spot_price = 119.0
    sig = strategy.evaluate(f, b, m, fu)
    assert sig is None
    assert strategy.state["BULLISH"].get("confirm_count", 0) == 0

    # 2. CALL live price above trigger for one poll -> no signal
    f.spot_price = 120.0
    sig = strategy.evaluate(f, b, m, fu)
    assert sig is None
    assert strategy.state["BULLISH"].get("confirm_count") == 1

    # 3. CALL live price above trigger for second consecutive poll -> breakout confirmed
    sig = strategy.evaluate(f, b, m, fu)
    assert sig is not None
    assert sig.direction == TradeDirection.BULLISH
    assert sig.spot_reference_price == 120.0


def test_call_breakout_reset_on_price_drop():
    f, b, m, fu = setup(bear=False)
    strategy = TrendPullbackStrategy(breakout_confirm_polls=2)

    # First poll passes
    f.spot_price = 120.0
    assert strategy.evaluate(f, b, m, fu) is None
    assert strategy.state["BULLISH"]["confirm_count"] == 1

    # Second poll falls below trigger -> confirmation counter resets
    f.spot_price = 118.5
    assert strategy.evaluate(f, b, m, fu) is None
    assert strategy.state["BULLISH"]["confirm_count"] == 0

    # Next poll above trigger starts at 1 again
    f.spot_price = 120.0
    assert strategy.evaluate(f, b, m, fu) is None
    assert strategy.state["BULLISH"]["confirm_count"] == 1


def test_put_breakout_live_price_and_polling():
    f, b, m, fu = setup(bear=True)
    # trigger price is b[-1].low - 0.02 * atr = 101 - 0.2 = 100.8
    strategy = TrendPullbackStrategy(breakout_confirm_polls=2)

    # Live price above trigger -> no signal
    f.spot_price = 101.5
    assert strategy.evaluate(f, b, m, fu) is None
    assert strategy.state["BEARISH"].get("confirm_count", 0) == 0

    # One poll below trigger -> no signal
    f.spot_price = 100.0
    assert strategy.evaluate(f, b, m, fu) is None
    assert strategy.state["BEARISH"]["confirm_count"] == 1

    # Second consecutive poll below trigger -> breakout confirmed
    sig = strategy.evaluate(f, b, m, fu)
    assert sig is not None
    assert sig.direction == TradeDirection.BEARISH
    assert sig.spot_reference_price == 100.0


def test_new_setup_resets_confirmation_counter():
    f, b, m, fu = setup(bear=False)
    s = TrendPullbackStrategy(breakout_confirm_polls=2)

    def make_bars(height, start_time):
        h = 100.0 + height
        raw = [(102, 104, 101, 103), (103, 104, 100.0, 102), (102, 100 + height*0.5, 101, 100 + height*0.5), (100 + height*0.5, h, 100 + height*0.4, h - 0.2), (h - 0.5, h - 1.0, h - 2.5, h - 2.0), (h - 2.0, h - 1.5, h - 3.5, h - 3.0), (h - 3.0, h - 1.2, h - 2.8, h - 1.5)]
        return [Candle(instrument_id='INDEX', interval='5m', start_time=start_time+timedelta(minutes=5*i), end_time=start_time+timedelta(minutes=5*(i+1)), open=o, high=hi, low=l, close=c, volume=0) for i, (o, hi, l, c) in enumerate(raw)]

    t1 = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    b1 = make_bars(20.0, t1)
    m1 = [b1[-1].model_copy(update={'interval':'15m', 'start_time':b1[-1].end_time-timedelta(minutes=15)})]
    fu1 = [c.model_copy(update={'instrument_id':'FUT', 'volume':100}) for c in b1]
    f.ema9_5m = 117.0; f.ema20_5m = 117.0

    # Poll 1 on setup 1
    f.timestamp = b1[-1].end_time; f.spot_price = b1[-1].high + 1.0
    assert s.evaluate(f, b1, m1, fu1) is None
    assert s.state['BULLISH']['confirm_count'] == 1

    # Poll 1 on setup 2 (new setup at t2)
    t2 = datetime(2026, 9, 17, 5, 0, tzinfo=timezone.utc)
    b2 = make_bars(25.0, t2)
    m2 = [b2[-1].model_copy(update={'interval':'15m', 'start_time':b2[-1].end_time-timedelta(minutes=15)})]
    fu2 = [c.model_copy(update={'instrument_id':'FUT', 'volume':100}) for c in b2]
    f.timestamp = b2[-1].end_time; f.spot_price = b2[-1].high + 1.0; f.ema9_5m = 120.0
    s.state['BULLISH'].pop('impulse', None)

    assert s.evaluate(f, b2, m2, fu2) is None
    # confirm_count should be 1 for the new setup, NOT 2!
    assert s.state['BULLISH']['confirm_count'] == 1

    # Poll 2 on setup 2 triggers!
    sig = s.evaluate(f, b2, m2, fu2)
    assert sig is not None


# ==============================================================================
# ITERATION 2 — Threshold Tests
# ==============================================================================

def test_impulse_thresholds():
    # 0.69 ATR impulse -> reject; 0.70 ATR impulse -> accept
    f, b, m, fu = setup(bear=False)
    def make_bars(height):
        start = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc); h = 100.0 + height
        raw = [(102, 104, 101, 103), (103, 104, 100.0, 102), (102, 100 + height*0.5, 101, 100 + height*0.5), (100 + height*0.5, h, 100 + height*0.4, h - 0.2), (h - 0.5, h - 1.0, h - 2.5, h - 2.0), (h - 2.0, h - 1.5, h - 3.5, h - 3.0), (h - 3.0, h - 1.2, h - 2.8, h - 1.5)]
        return [Candle(instrument_id='INDEX', interval='5m', start_time=start+timedelta(minutes=5*i), end_time=start+timedelta(minutes=5*(i+1)), open=o, high=hi, low=l, close=c, volume=0) for i, (o, hi, l, c) in enumerate(raw)]

    b_69 = make_bars(6.9); b_70 = make_bars(7.0)
    f.timestamp = b_69[-1].end_time; f.ema9_5m = 105.0; f.ema20_5m = 105.0; f.spot_price = 109.0
    fu_69 = [c.model_copy(update={'instrument_id':'FUT', 'volume':100}) for c in b_69]
    fu_70 = [c.model_copy(update={'instrument_id':'FUT', 'volume':100}) for c in b_70]
    m_69 = [b_69[-1].model_copy(update={'interval':'15m', 'start_time':b_69[-1].end_time-timedelta(minutes=15)})]
    m_70 = [b_70[-1].model_copy(update={'interval':'15m', 'start_time':b_70[-1].end_time-timedelta(minutes=15)})]

    s1 = TrendPullbackStrategy(breakout_confirm_polls=1)
    s2 = TrendPullbackStrategy(breakout_confirm_polls=1)
    assert s1.evaluate(f, b_69, m_69, fu_69) is None
    assert s2.evaluate(f, b_70, m_70, fu_70) is not None


def test_pullback_depth_thresholds():
    # 7% pullback -> reject; 8% pullback -> accept; 70% pullback -> accept; 71% pullback -> reject
    f, b, m, fu = setup(bear=False)

    # 7% depth -> reject (extreme = 120 - 1.4 = 118.6)
    b_7 = [c.model_copy() for c in b]
    b_7[4] = b_7[4].model_copy(update={"low": 118.6, "high": 119.5})
    b_7[5] = b_7[5].model_copy(update={"low": 118.6, "high": 119.2})
    b_7[6] = b_7[6].model_copy(update={"low": 118.6, "high": 119.0})
    f.ema9_5m = 118.6
    f.spot_price = 120.5
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b_7, m, fu) is None

    # 8% depth -> accept (extreme = 120 - 1.6 = 118.4)
    b_8 = [c.model_copy() for c in b]
    b_8[4] = b_8[4].model_copy(update={"low": 118.4, "high": 119.5})
    b_8[5] = b_8[5].model_copy(update={"low": 118.4, "high": 119.2})
    b_8[6] = b_8[6].model_copy(update={"low": 118.4, "high": 119.0})
    f.ema9_5m = 118.4
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b_8, m, fu) is not None

    # 70% depth -> accept (extreme = 120 - 14.0 = 106.0)
    b_70 = [c.model_copy() for c in b]
    b_70[4] = b_70[4].model_copy(update={"low": 106.0, "high": 114.0})
    b_70[5] = b_70[5].model_copy(update={"low": 106.0, "high": 112.0})
    b_70[6] = b_70[6].model_copy(update={"low": 106.5, "high": 110.0})
    f.ema9_5m = 106.0
    f.spot_price = 115.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b_70, m, fu) is not None

    # 71% depth -> reject (extreme = 120 - 14.2 = 105.8)
    b_71 = [c.model_copy() for c in b]
    b_71[4] = b_71[4].model_copy(update={"low": 105.8, "high": 114.0})
    b_71[5] = b_71[5].model_copy(update={"low": 105.8, "high": 112.0})
    b_71[6] = b_71[6].model_copy(update={"low": 106.0, "high": 110.0})
    f.ema9_5m = 105.8
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b_71, m, fu) is None


def test_retest_distance_thresholds():
    # 0.45 ATR retest distance -> accept; > 0.45 ATR -> reject
    f, b, m, fu = setup(bear=False)
    fu_no_vwap = [c.model_copy(update={'volume': 0}) for c in fu]
    f.ema20_5m = 50.0
    f.futures_vwap = 50.0

    # 0.45 ATR distance -> accept (112 - 4.5 = 107.5)
    f.ema9_5m = 112.0 - 4.5
    assert TrendPullbackStrategy(breakout_confirm_polls=1, min_available_confirmations=1, min_confirmation_score=1).evaluate(f, b, m, fu_no_vwap) is not None

    # 0.46 ATR distance -> reject (112 - 4.6 = 107.4)
    f.ema9_5m = 112.0 - 4.6
    assert TrendPullbackStrategy(breakout_confirm_polls=1, min_available_confirmations=1, min_confirmation_score=1).evaluate(f, b, m, fu_no_vwap) is None


# ==============================================================================
# ITERATION 3 — Confirmation & Risk Tests
# ==============================================================================

def test_ternary_confirmation_handling():
    f, b, m, fu = setup(bear=False)
    fu_no_vol = [c.model_copy(update={'volume': 0}) for c in fu]

    # Case 1: 2 PASS / 3 available -> passes
    f.supertrend_direction = "BULLISH"  # Pass
    f.futures_price = 148; f.futures_vwap = 140  # Pass
    f.rvol_5m = 0.5  # Fail (< 1.20)
    f.bull_derivatives_score = None  # None
    f.futures_buildup = None  # None
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is not None

    # Case 2: 3 PASS / 3 available -> passes with the natural >= 1.0 derivative threshold
    f.bull_derivatives_score = 1.0  # Pass (>= 1.0)
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is not None

    # Case 3: 1 PASS / 3 available -> fails
    f.supertrend_direction = "BEARISH"  # Now only vwap passes
    f.bull_derivatives_score = 0.0
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is None

    # Case 4: 2 PASS / 2 available -> passes with the minimum 2 available requirement
    f.supertrend_direction = "BULLISH"  # Pass
    f.bull_derivatives_score = None  # None
    f.rvol_5m = None  # None -> only supertrend and vwap available (2 available)
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is not None

    # Case 5: 1 PASS / 2 available -> fails because two passes are still required
    f.futures_price = 130.0  # Futures VWAP confirmation now fails
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is None

    # Case 6: UNKNOWN confirmations excluded rather than counted as failures
    f.futures_price = 148.0
    f.futures_buildup = "UNKNOWN"
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu_no_vol) is not None


def test_risk_boundary_no_lower_floor():
    f, b, m, fu = setup(bear=False)
    # extreme = 112, atr = 10, raw_stop = 112 - 1.5 = 110.5.

    # 1. risk = 0 -> reject (live_price = 110.5)
    f.spot_price = 110.5
    assert TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu, ThresholdOverrides(breakout_buffer_atr=-1.0)) is None

    # 2. risk = 0.20 ATR (2.0 pts) -> accept (live_price = 112.5)
    f.spot_price = 112.5
    sig = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu, ThresholdOverrides(breakout_buffer_atr=-1.0))
    assert sig is not None
    assert sig.r_points == pytest.approx(2.0)

    # 3. risk = 1.60 ATR (16.0 pts) -> accept (live_price = 126.5)
    f.spot_price = 126.5
    sig = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu)
    assert sig is not None
    assert sig.r_points == pytest.approx(16.0)

    # 4. risk > 1.60 ATR (e.g. 16.5 pts) -> reject (live_price = 127.0)
    f.spot_price = 127.0
    sig = TrendPullbackStrategy(breakout_confirm_polls=1).evaluate(f, b, m, fu)
    assert sig is None


def test_impulse_detection_fallback():
    t0 = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    atr = 10.0

    # 1. CALL: lowest low before highest high, impulse = 0.70 ATR (7.0 pts) -> PASS
    call_bars_pass = [
        Candle(instrument_id="INDEX", interval="5m",
               start_time=t0 + timedelta(minutes=5*i), end_time=t0 + timedelta(minutes=5*(i+1)),
               open=100.0 + i, high=101.0 + (6.0 if i == 5 else i), low=100.0 if i == 0 else 100.0 + i,
               close=100.5 + i, volume=100)
        for i in range(6)
    ]
    fb_call = TrendPullbackStrategy._fallback_impulse(call_bars_pass, atr=atr, bullish=True)
    assert fb_call is not None
    assert fb_call["source"] == "FALLBACK"
    assert fb_call["height"] == pytest.approx(7.0)
    assert fb_call["low"] == 100.0
    assert fb_call["high"] == 107.0

    # CALL: lowest low before highest high, impulse = 0.69 ATR (6.9 pts) -> FAIL
    call_bars_fail_size = [
        Candle(instrument_id="INDEX", interval="5m",
               start_time=t0 + timedelta(minutes=5*i), end_time=t0 + timedelta(minutes=5*(i+1)),
               open=100.0 + i, high=100.0 + (6.9 if i == 5 else i), low=100.0 if i == 0 else 100.0 + i,
               close=100.5 + i, volume=100)
        for i in range(6)
    ]
    assert TrendPullbackStrategy._fallback_impulse(call_bars_fail_size, atr=atr, bullish=True) is None

    # CALL: highest high occurs before lowest low -> CALL fallback FAIL
    call_bars_inverted = [
        Candle(instrument_id="INDEX", interval="5m",
               start_time=t0 + timedelta(minutes=5*i), end_time=t0 + timedelta(minutes=5*(i+1)),
               open=110.0 - i, high=115.0 if i == 0 else 110.0 - i, low=95.0 if i == 5 else 109.0 - i,
               close=109.5 - i, volume=100)
        for i in range(6)
    ]
    assert TrendPullbackStrategy._fallback_impulse(call_bars_inverted, atr=atr, bullish=True) is None

    # 2. PUT: highest high before lowest low, impulse >= 0.70 ATR -> PASS
    put_bars_pass = [
        Candle(instrument_id="INDEX", interval="5m",
               start_time=t0 + timedelta(minutes=5*i), end_time=t0 + timedelta(minutes=5*(i+1)),
               open=110.0 - i, high=110.0 if i == 0 else 109.0 - i, low=102.0 if i == 5 else 108.0 - i,
               close=108.5 - i, volume=100)
        for i in range(6)
    ]
    fb_put = TrendPullbackStrategy._fallback_impulse(put_bars_pass, atr=atr, bullish=False)
    assert fb_put is not None
    assert fb_put["source"] == "FALLBACK"
    assert fb_put["height"] == pytest.approx(8.0)
    assert fb_put["high"] == 110.0
    assert fb_put["low"] == 102.0

    # 3. Existing pivot impulse PASS -> pivot impulse remains preferred
    f, b, m, fu = setup(bear=False)
    strat = TrendPullbackStrategy(breakout_confirm_polls=1)
    diag = strat.diagnose(f, b, m, futures_candles=fu)[0]
    assert diag.phase_summary["impulse"]["source"] == "PIVOT"
    assert diag.phase_summary["impulse_source"] == "PIVOT"

    # 4. Pivot impulse FAIL, Fallback PASS -> fallback is used
    # In call_bars_pass, each bar is strictly ascending so no confirmed pivot high/low exists
    assert TrendPullbackStrategy._impulse(call_bars_pass, atr=atr, bullish=True) is None
    assert TrendPullbackStrategy._fallback_impulse(call_bars_pass, atr=atr, bullish=True) is not None

    # 5. Pivot FAIL, Fallback FAIL -> no impulse
    flat_bars = [
        Candle(instrument_id="INDEX", interval="5m",
               start_time=t0 + timedelta(minutes=5*i), end_time=t0 + timedelta(minutes=5*(i+1)),
               open=100.0, high=101.0, low=99.0, close=100.0, volume=100)
        for i in range(6)
    ]
    assert TrendPullbackStrategy._impulse(flat_bars, atr=atr, bullish=True) is None
    assert TrendPullbackStrategy._fallback_impulse(flat_bars, atr=atr, bullish=True) is None


def test_candle_contiguity_tolerance():
    t0 = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    c0 = Candle(instrument_id="INDEX", interval="5m",
                start_time=t0, end_time=t0 + timedelta(minutes=5),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=100)

    # 5m tests:
    # 300 sec -> PASS
    c_300 = c0.model_copy(update={"start_time": t0 + timedelta(seconds=300)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0, c_300, "5m")
    assert ok is True and sec == 300.0

    # 302 sec -> PASS
    c_302 = c0.model_copy(update={"start_time": t0 + timedelta(seconds=302)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0, c_302, "5m")
    assert ok is True and sec == 302.0

    # 305 sec -> PASS
    c_305 = c0.model_copy(update={"start_time": t0 + timedelta(seconds=305)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0, c_305, "5m")
    assert ok is True and sec == 305.0

    # 306 sec -> FAIL
    c_306 = c0.model_copy(update={"start_time": t0 + timedelta(seconds=306)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0, c_306, "5m")
    assert ok is False and sec == 306.0

    # 600 sec -> FAIL
    c_600 = c0.model_copy(update={"start_time": t0 + timedelta(seconds=600)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0, c_600, "5m")
    assert ok is False and sec == 600.0

    # 15m tests:
    c0_15m = Candle(instrument_id="INDEX", interval="15m",
                    start_time=t0, end_time=t0 + timedelta(minutes=15),
                    open=100.0, high=101.0, low=99.0, close=100.0, volume=100)

    # 900 sec -> PASS
    c_900 = c0_15m.model_copy(update={"start_time": t0 + timedelta(seconds=900)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0_15m, c_900, "15m")
    assert ok is True and sec == 900.0

    # 903 sec -> PASS
    c_903 = c0_15m.model_copy(update={"start_time": t0 + timedelta(seconds=903)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0_15m, c_903, "15m")
    assert ok is True and sec == 903.0

    # 905 sec -> PASS
    c_905 = c0_15m.model_copy(update={"start_time": t0 + timedelta(seconds=905)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0_15m, c_905, "15m")
    assert ok is True and sec == 905.0

    # 906 sec -> FAIL
    c_906 = c0_15m.model_copy(update={"start_time": t0 + timedelta(seconds=906)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0_15m, c_906, "15m")
    assert ok is False and sec == 906.0

    # 1800 sec -> FAIL
    c_1800 = c0_15m.model_copy(update={"start_time": t0 + timedelta(seconds=1800)})
    ok, sec = TrendPullbackStrategy.validate_candle_contiguity(c0_15m, c_1800, "15m")
    assert ok is False and sec == 1800.0


def test_strategy_candle_contiguity_gate():
    f, b, m, fu = setup(bear=False)
    # Mutate bar start time by 2 seconds (302s total interval) -> should PASS with tolerance
    b[-1] = b[-1].model_copy(update={"start_time": b[-2].start_time + timedelta(seconds=302)})
    strat = TrendPullbackStrategy(breakout_confirm_polls=1)
    diag = strat.diagnose(f, b, m, futures_candles=fu)[0]
    assert diag.phase_summary["candle_contiguity_pass"] is True
    assert diag.phase_summary["candle_interval_seconds"] == 302.0

    # Mutate bar start time by 6 seconds (306s total interval) -> should FAIL contiguity
    b[-1] = b[-1].model_copy(update={"start_time": b[-2].start_time + timedelta(seconds=306)})
    diag_fail = strat.diagnose(f, b, m, futures_candles=fu)[0]
    assert diag_fail.phase_summary["candle_contiguity_pass"] is False
    assert diag_fail.phase_summary["candle_interval_seconds"] == 306.0
    assert "incomplete candle sequence" in diag_fail.key_blocker
    assert diag_fail.phase_summary["primary_blocker"] == diag_fail.key_blocker
