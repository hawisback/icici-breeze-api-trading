from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import MarketFeatures, ThresholdOverrides, TradeDirection, RiskConfig
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy
from services.strategy.position_manager import PositionManager


def setup():
    start = datetime(2026,9,18,4,0,tzinfo=timezone.utc)
    bars = [Candle(instrument_id="SPOT",interval="5m",source="BREEZE",
                   start_time=start+timedelta(minutes=5*i),end_time=start+timedelta(minutes=5*(i+1)),
                   open=100,high=103,low=97,close=100,volume=0) for i in range(8)]
    f = MarketFeatures(timestamp=bars[-1].end_time,spot_price=999,atr_5m=5,
                       breakout_data_ready=True,bb_width_percentile=20,
                       rvol_5m=0,futures_price=110,futures_vwap=105)
    return f,bars


def advance(f,bars,bear=False,close=None):
    c = bars[-1].model_copy(update={"start_time":bars[-1].end_time,
        "end_time":bars[-1].end_time+timedelta(minutes=5),
        "open":98 if bear else 102,"high":99 if bear else 104.2,
        "low":95.8 if bear else 101,"close":close if close is not None else (96 if bear else 104)})
    bars.append(c)
    f.timestamp = c.end_time
    f.bb_width_percentile = 95  # expansion must not unlock the valid box
    if bear:
        f.futures_price = 100


@pytest.mark.parametrize("bear",[False,True])
def test_breakout_parity_frozen_r_and_restart_dedup(bear):
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    before = strat.export_state()
    assert strat.diagnose(f,bars)[0].phase_state == "BOX_LOCKED"
    assert strat.export_state() == before  # diagnostics are read-only
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box.atr_at_lock == 5  # no floor of 10
    saved = strat.export_state()
    strat = VolatilityBreakoutStrategy()
    strat.restore_state(saved)
    advance(f,bars,bear)
    diag = strat.diagnose(f,bars)[int(bear)]
    assert diag.overall_status == "READY_TO_TRIGGER"  # RVOL is not mandatory
    signal = strat.evaluate(f,bars)
    assert signal.direction == (TradeDirection.BEARISH if bear else TradeDirection.BULLISH)
    assert signal.spot_reference_price == bars[-1].close != f.spot_price
    assert signal.r_points == pytest.approx(2.25)
    assert signal.timestamp == bars[-1].end_time
    assert signal.features_snapshot["confirmation_score"] == 3
    assert strat.evaluate(f,bars) is None
    restored = VolatilityBreakoutStrategy()
    restored.restore_state(strat.export_state())
    assert restored.evaluate(f,bars) is None


def test_box_ages_only_on_completed_bars_and_expires_after_eight():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    high = strat.locked_box.box_high
    for i in range(1,10):
        last = bars[-1]
        bars.append(last.model_copy(update={"start_time":last.end_time,"end_time":last.end_time+timedelta(minutes=5)}))
        f.timestamp = bars[-1].end_time
        f.bb_width_percentile = 99
        for _ in range(5):
            strat.diagnose(f,bars)
            strat.evaluate(f,bars)
        if i <= 8:
            assert strat.locked_box.bars_active == i
            assert strat.locked_box.box_high == high
        else:
            assert strat.locked_box is None


@pytest.mark.parametrize("fault",["stale","synthetic","incomplete","duplicate","gap","config"])
def test_invalid_data_and_config_reset_box(fault):
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    overrides = None
    if fault == "stale": f.timestamp += timedelta(minutes=5)
    if fault == "synthetic": bars[-1] = bars[-1].model_copy(update={"source":"SIMULATED"})
    if fault == "incomplete": f.timestamp -= timedelta(seconds=1)
    if fault == "duplicate": bars.append(bars[-1])
    if fault == "gap":
        bars[-1] = bars[-1].model_copy(update={"start_time":bars[-1].start_time+timedelta(minutes=5),
                                             "end_time":bars[-1].end_time+timedelta(minutes=5)})
        f.timestamp = bars[-1].end_time
    if fault == "config": overrides = ThresholdOverrides(bb_width_percentile=30)
    assert strat.evaluate(f,bars,overrides=overrides) is None
    assert strat.locked_box is None


def test_confirmation_overrides_do_not_leak_from_strategy_a_and_wall_penalty():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    override = ThresholdOverrides(min_confirmation_score=6)
    strat.evaluate(f,bars,overrides=override)
    advance(f,bars)
    assert strat.evaluate(f,bars,overrides=override) is not None
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    f.bullish_oi_wall = True
    diag = strat.diagnose(f,bars)[0]
    assert diag.phase_summary["confirmation"]["score"] == 2
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box is None


def test_overextension_abandons_box():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars,close=107)
    bars[-1] = bars[-1].model_copy(update={"high":107.1})
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box is None


def test_empirical_bb_rank_and_clean_oi_evidence():
    wide = [100+(-1)**i*5 for i in range(80)]
    narrow = wide+[100+(-1)**i*.01 for i in range(20)]
    assert FeatureEngine.calculate_bb_percentile(narrow) < 25
    assert FeatureEngine.calculate_bb_percentile([100]*50) == 50
    assert FeatureEngine.calculate_bb_percentile([100]*20) == 100
    chain = {"source":"BREEZE","strikes":[{"strike":i,
             "call":{"open_interest":100 if i != 101 else 1000,"oi_change":-10},
             "put":{"open_interest":100,"oi_change":20,"oi_velocity":1}} for i in range(95,106)]}
    bull,bear,wall,_ = FeatureEngine.breakout_oi_features(chain,100.5,"LONG_BUILDUP")
    assert (bull,bear,wall) == (5,0,True)
    chain["source"] = "SIMULATED"
    assert FeatureEngine.breakout_oi_features(chain,100.5,"NEUTRAL") == (0,0,False,False)


def test_maximum_lots_cap_and_missing_lot_rejection():
    pm = PositionManager(RiskConfig(max_lots_per_trade=2))
    assert pm.calculate_position_size(60,500000,25) == (2,50)
    assert pm.calculate_position_size(60,500000) == (0,0)


@pytest.mark.parametrize("bear",[False,True])
def test_false_breakout_counts_distinct_closes_not_polls(bear):
    from services.strategy.models import ActiveTrade, AutoTradingMode, StrategyName, OptionType, SessionTimersConfig
    f,bars = setup()
    t = ActiveTrade(trade_id="B",mode=AutoTradingMode.PAPER,strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BEARISH if bear else TradeDirection.BULLISH,
        option_type=OptionType.PUT if bear else OptionType.CALL,contract_symbol="TEST",contract_instrument_id="TEST",
        expiry="2026-09-24",strike=100,quantity=25,lot_size=25,lots=1,entry_time=f.timestamp,
        entry_option_price=60,entry_spot_price=96 if bear else 104,initial_structural_stop=105 if bear else 95,
        initial_r_points=9,box_high=103,box_low=97,atr_at_lock=5,current_trailing_stop=105 if bear else 95,
        current_option_price=60,current_spot_price=96 if bear else 104,option_hard_stop_price=45)
    pm = PositionManager(session_config=SessionTimersConfig(force_exit_time="23:59"))
    f.spot_price = 97.2 if bear else 102.8
    f.futures_price = f.futures_vwap = 100
    f.ema9_5m = f.ema20_5m = 100
    f.supertrend_direction = "NEUTRAL"
    f.timestamp += timedelta(minutes=5)
    f.closed_5m_time = f.timestamp
    f.closed_5m_price = f.spot_price
    for _ in range(5):
        assert pm.update_position(t,60,f,as_of=f.timestamp)[1] is None
    assert t.consecutive_inside_box_closes == 1
    # Intrabar movement alone does not increment the counter.
    f.spot_price = 97.4 if bear else 102.6
    assert pm.update_position(t,60,f,as_of=f.timestamp)[1] is None
    f.timestamp += timedelta(minutes=5)
    f.closed_5m_time = f.timestamp
    assert "FALSE_BREAKOUT_EXIT" in pm.update_position(t,60,f,as_of=f.timestamp)[1]


@pytest.mark.asyncio
async def test_box_repository_round_trip_is_separate_from_strategy_a(tmp_path):
    from services.strategy.repository import StrategyRepository
    repo = StrategyRepository(db_path=tmp_path/"strategy.db")
    await repo.initialize()
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    await repo.save_runtime({"BULLISH":{}}, "trend_pullback")
    await repo.save_runtime(strat.export_state(), "volatility_breakout")
    restored = VolatilityBreakoutStrategy()
    restored.restore_state(await repo.get_runtime("volatility_breakout"))
    assert restored.export_state() == strat.export_state()
    assert await repo.get_runtime() == {"BULLISH":{}}


def test_breakout_readiness_does_not_require_strategy_a_macro_warmup():
    f,bars = setup()
    start = bars[0].start_time
    bars = [bars[0].model_copy(update={"start_time":start+timedelta(minutes=i*5),
             "end_time":start+timedelta(minutes=(i+1)*5),"volume":100}) for i in range(40)]
    futures = [c.model_copy(update={"instrument_id":"FUT"}) for c in bars]
    result = FeatureEngine.compute_all_features(bars,[],futures,spot_price=100,as_of=bars[-1].end_time)
    assert result.breakout_data_ready
    assert not result.data_ready


@pytest.mark.asyncio
async def test_unfilled_entry_timeout_cancels_without_assuming_a_fill():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from libs.contracts.models import utc_now
    from services.strategy.models import TradeLifecycleState
    from services.strategy.service import StrategyService
    pending = SimpleNamespace(status=SimpleNamespace(value="OPEN"),filled_quantity=0,
                              broker_order_id="broker-id",trading_mode="LIVE")
    gateway = Mock(cancel_order=AsyncMock())
    oms = Mock(get_order=AsyncMock(return_value=pending))
    svc = StrategyService(oms,historical_service=SimpleNamespace(broker_gateway=gateway))
    trade = SimpleNamespace(state=TradeLifecycleState.ENTRY_PENDING,entry_order_id="order",
                            entry_time=utc_now()-timedelta(seconds=30))
    await svc._evaluate_active_trade(trade,MarketFeatures(spot_price=100))
    gateway.cancel_order.assert_awaited_once_with("broker-id",mode="LIVE")
    assert trade.state == TradeLifecycleState.ENTRY_PENDING
