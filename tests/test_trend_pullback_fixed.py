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
                                   "volume":50 if i in (4,5) else 1000 if i==6 else 100}) for i,c in enumerate(bars)]
    macro = [bars[-1].model_copy(update={"interval":"15m", "start_time":bars[-1].end_time-timedelta(minutes=15)})]
    features = MarketFeatures(timestamp=bars[-1].end_time, spot_price=999, data_ready=True, data_reason="",
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
    strategy = TrendPullbackStrategy()
    diag = strategy.diagnose(f,b,m,futures_candles=fu)[int(bear)]
    assert diag.overall_status == "READY_TO_TRIGGER"
    assert strategy.export_state() == {"BULLISH":{}, "BEARISH":{}}
    signal = strategy.evaluate(f,b,m,fu)
    assert signal.direction == (TradeDirection.BEARISH if bear else TradeDirection.BULLISH)
    assert signal.spot_reference_price == b[-1].close != f.spot_price
    assert signal.features_snapshot["pullback_bars"] == 2
    assert signal.features_snapshot["pullback_vol_ratio"] == .5
    assert signal.features_snapshot["confirmation_score"] == f'{diag.phase_summary["confirmation"]["score"]}/6'
    assert signal.r_points == pytest.approx(7.5)
    restored = TrendPullbackStrategy()
    restored.restore_state(strategy.export_state())
    assert restored.evaluate(f,b,m,fu) is None


def test_rvol_override_changes_decision():
    f,b,m,fu = setup()
    # Supertrend + VWAP + pullback volume = 3. RVOL provides the fourth.
    assert TrendPullbackStrategy().evaluate(f,b,m,fu,ThresholdOverrides(min_confirmation_score=4,rvol_threshold=1.2))
    assert TrendPullbackStrategy().evaluate(f,b,m,fu,ThresholdOverrides(min_confirmation_score=4,rvol_threshold=1.3)) is None


def test_raw_positive_slope_does_not_bypass_normalized_threshold():
    f,b,m,fu = setup()
    f.plus_di_15m = 0
    f.ema20_slope_norm_15m = .01
    f.ema20_slope_15m = 1
    assert TrendPullbackStrategy().evaluate(f,b,m,fu) is None
    assert TrendPullbackStrategy(ema_slope_threshold=.01).evaluate(f,b,m,fu)


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


@pytest.mark.parametrize("count,accepted", [(1,False),(2,True),(9,True),(10,False)])
def test_pullback_age_boundaries(count, accepted):
    f,b,m,fu = setup()
    raw = b[:4] + [b[5]]*count + [b[-1]]
    start = b[0].start_time
    bars = [c.model_copy(update={"start_time":start+timedelta(minutes=5*i),
                                 "end_time":start+timedelta(minutes=5*(i+1))}) for i,c in enumerate(raw)]
    futures = [c.model_copy(update={"instrument_id":"FUT", "volume":100}) for c in bars]
    f.timestamp = bars[-1].end_time
    m = [m[0].model_copy(update={"end_time":f.timestamp, "start_time":f.timestamp-timedelta(minutes=15)})]
    strategy = TrendPullbackStrategy()
    signal = strategy.evaluate(f,bars,m,futures)
    assert bool(signal) is accepted
    if count == 10:
        assert "impulse" not in strategy.state["BULLISH"]


def test_no_atr_floor():
    f,b,m,fu = setup()
    def scale(c):
        return c.model_copy(update={key:getattr(c,key)/2 for key in ("open","high","low","close")})
    b,m,fu = list(map(scale,b)),list(map(scale,m)),list(map(scale,fu))
    for field in ("atr_5m","futures_atr_5m","ema9_5m","ema20_5m","ema20_15m","ema50_15m","futures_price","futures_vwap"):
        setattr(f,field,getattr(f,field)/2)
    sig = TrendPullbackStrategy().evaluate(f,b,m,fu)
    assert sig.r_points == pytest.approx(3.75)
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
    strategy = TrendPullbackStrategy()
    strategy.reset(f.timestamp)
    assert strategy.evaluate(f,b,m,fu) is None
    assert strategy.state["BULLISH"]["after"] == f.timestamp.isoformat()


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
