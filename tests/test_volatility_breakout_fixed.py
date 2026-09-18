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
    f = MarketFeatures(timestamp=bars[-1].end_time,spot_price=100,atr_5m=5,
                       breakout_data_ready=True,bb_width_percentile=20,
                       rvol_5m=0,futures_price=110,futures_vwap=105)
    return f,bars


def advance(f,bars,bear=False,close=None):
    cl = close if close is not None else (96 if bear else 104)
    op = 98 if bear else 102
    hi = max(99 if bear else 104.2, cl + 0.1, op + 0.1)
    lo = min(95.8 if bear else 101, cl - 0.1, op - 0.1)
    c = bars[-1].model_copy(update={"start_time":bars[-1].end_time,
        "end_time":bars[-1].end_time+timedelta(minutes=5),
        "open":op,"high":hi,"low":lo,"close":cl})
    bars.append(c)
    f.timestamp = c.end_time
    f.bb_width_percentile = 95  # expansion must not unlock the valid box
    if bear:
        f.futures_price = 100
    f.spot_price = cl


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
    # Poll 1: Breakout detected, waiting for 2nd poll confirmation
    assert strat.evaluate(f,bars) is None
    assert strat.breakout_confirm_count == 1
    # Poll 2: Breakout confirmed
    diag = strat.diagnose(f,bars)[int(bear)]
    assert diag.overall_status == "READY_TO_TRIGGER"  # RVOL is not mandatory
    signal = strat.evaluate(f,bars)
    assert signal.direction == (TradeDirection.BEARISH if bear else TradeDirection.BULLISH)
    assert signal.spot_reference_price == f.spot_price
    assert signal.r_points == pytest.approx(2.25)
    assert signal.timestamp == bars[-1].end_time
    assert signal.features_snapshot["confirmation_score"] == 3
    assert strat.evaluate(f,bars) is None
    restored = VolatilityBreakoutStrategy()
    restored.restore_state(strat.export_state())
    assert restored.evaluate(f,bars) is None


def test_box_ages_only_on_completed_bars_and_expires_after_twelve():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    high = strat.locked_box.box_high
    for i in range(1,15):
        last = bars[-1]
        bars.append(last.model_copy(update={"start_time":last.end_time,"end_time":last.end_time+timedelta(minutes=5)}))
        f.timestamp = bars[-1].end_time
        f.spot_price = 100  # inside consolidation box
        f.bb_width_percentile = 99
        for _ in range(5):
            strat.diagnose(f,bars)
            strat.evaluate(f,bars)
        if i <= 12:
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
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    override = ThresholdOverrides(min_confirmation_score=6)
    strat.evaluate(f,bars,overrides=override)
    advance(f,bars)
    assert strat.evaluate(f,bars,overrides=override) is not None
    f,bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f,bars)
    advance(f,bars)
    f.bullish_oi_wall = True
    diag = strat.diagnose(f,bars)[0]
    # Change 9: OI wall does NOT deduct points; informational only
    assert diag.phase_summary["confirmation"]["oi_wall_detected"] is True
    assert diag.phase_summary["confirmation"]["score"] == 3
    sig = strat.evaluate(f,bars)
    assert sig is not None
    assert sig.features_snapshot["oi_wall_detected"] is True


def test_overextension_abandons_box():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    # Box high is 103, ATR is 5. Max extension 0.90 * 5 = 4.5. 103 + 4.5 = 107.5.
    # Close at 107 is within extension; 108 is overextended (> 107.5).
    advance(f,bars,close=108)
    bars[-1] = bars[-1].model_copy(update={"high":108.1})
    f.spot_price = 108
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


def test_bb_compression_thresholds_and_lookback():
    import inspect
    sig = inspect.signature(FeatureEngine.calculate_bb_percentile)
    assert sig.parameters["history"].default == 60

    strat = VolatilityBreakoutStrategy()
    assert strat.bb_percentile_lookback == 60
    assert strat.bb_width_percentile_threshold == 35.0

    f, bars = setup()
    # 34th percentile: passes compression
    f.bb_width_percentile = 34.0
    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"

    # 35th percentile: passes compression (boundary <= 35)
    f.bb_width_percentile = 35.0
    strat_35 = VolatilityBreakoutStrategy()
    diag = strat_35.diagnose(f, bars)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"

    # 36th percentile: fails compression (> 35)
    f.bb_width_percentile = 36.0
    strat_36 = VolatilityBreakoutStrategy()
    diag = strat_36.diagnose(f, bars)[0]
    assert diag.phase_summary["compression_pass"] is False
    assert diag.phase_summary["primary_blocker"] == "NO_COMPRESSION"
    assert strat_36.evaluate(f, bars) is None
    assert strat_36.locked_box is None


def test_box_height_thresholds():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    assert strat.box_max_height_atr == 1.50

    # ATR is 5.0. Max box height = 1.50 * 5.0 = 7.50.
    # Height 7.45 (1.49 ATR): PASS
    bars_149 = [c.model_copy(update={"high": 103.70, "low": 96.25}) for c in bars]
    strat_149 = VolatilityBreakoutStrategy()
    diag = strat_149.diagnose(f, bars_149)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"

    # Height 7.50 (1.50 ATR): PASS
    bars_150 = [c.model_copy(update={"high": 103.75, "low": 96.25}) for c in bars]
    strat_150 = VolatilityBreakoutStrategy()
    diag = strat_150.diagnose(f, bars_150)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"

    # Height 7.55 (1.51 ATR): FAIL (BOX_TOO_LARGE)
    bars_151 = [c.model_copy(update={"high": 103.80, "low": 96.25}) for c in bars]
    strat_151 = VolatilityBreakoutStrategy()
    diag = strat_151.diagnose(f, bars_151)[0]
    assert diag.phase_summary["compression_pass"] is False
    assert diag.phase_summary["primary_blocker"] == "BOX_TOO_LARGE"
    assert strat_151.evaluate(f, bars_151) is None
    assert strat_151.locked_box is None


def test_live_price_breakout_trigger_and_polling():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f, bars)
    assert strat.locked_box is not None

    advance(f, bars)
    # Live spot_price moves to 103.20 (beyond 103.15 call_trigger)
    f.spot_price = 103.20

    # Poll 1: Breakout detected, but requires 2 matching polls
    diag1 = strat.diagnose(f, bars)[0]
    assert diag1.overall_status == "WAITING"
    assert diag1.phase_summary["primary_blocker"] == "BREAKOUT_NOT_CONFIRMED"
    assert strat.evaluate(f, bars) is None
    assert strat.breakout_confirm_count == 1
    assert strat.confirm_direction == TradeDirection.BULLISH

    # Price drops back inside box to 102.0
    f.spot_price = 102.0
    diag_drop = strat.diagnose(f, bars)[0]
    assert diag_drop.phase_summary["primary_blocker"] == "WAITING_FOR_BREAKOUT"
    assert strat.evaluate(f, bars) is None
    # Poll count reset on price drop, but box remains locked
    assert strat.breakout_confirm_count == 0
    assert strat.confirm_direction is None
    assert strat.locked_box is not None

    # Price rises again beyond trigger to 103.20
    f.spot_price = 103.20
    # Poll 1 of new attempt:
    assert strat.evaluate(f, bars) is None
    assert strat.breakout_confirm_count == 1
    # Poll 2 of new attempt:
    diag2 = strat.diagnose(f, bars)[0]
    assert diag2.overall_status == "READY_TO_TRIGGER"
    sig = strat.evaluate(f, bars)
    assert sig is not None
    assert sig.direction == TradeDirection.BULLISH
    assert sig.spot_reference_price == 103.20

    # Test PUT trigger with 2 polls
    f_bear, bars_bear = setup()
    strat_bear = VolatilityBreakoutStrategy()
    strat_bear.evaluate(f_bear, bars_bear)
    advance(f_bear, bars_bear, bear=True)
    # Live price drops to 96.80 (< 96.85 put_trigger)
    f_bear.spot_price = 96.80
    assert strat_bear.evaluate(f_bear, bars_bear) is None
    assert strat_bear.breakout_confirm_count == 1
    assert strat_bear.confirm_direction == TradeDirection.BEARISH
    sig_bear = strat_bear.evaluate(f_bear, bars_bear)
    assert sig_bear is not None
    assert sig_bear.direction == TradeDirection.BEARISH
    assert sig_bear.spot_reference_price == 96.80


def test_anti_chase_extension_thresholds():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f, bars)
    advance(f, bars)

    # 1. 0.89 ATR extension: 103 + 0.89 * 5 = 107.45 (PASS)
    f.spot_price = 107.45
    diag_89 = strat.diagnose(f, bars)[0]
    assert diag_89.phase_summary["extension"]["passed"] is True
    assert diag_89.overall_status == "READY_TO_TRIGGER"

    # 2. 0.90 ATR extension: 103 + 0.90 * 5 = 107.50 (PASS - boundary)
    f.spot_price = 107.50
    diag_90 = strat.diagnose(f, bars)[0]
    assert diag_90.phase_summary["extension"]["passed"] is True
    assert diag_90.overall_status == "READY_TO_TRIGGER"

    # 3. 0.91 ATR extension: 103 + 0.91 * 5 = 107.55 (FAIL - overextended)
    f.spot_price = 107.55
    diag_91 = strat.diagnose(f, bars)[0]
    assert diag_91.phase_summary["primary_blocker"] == "BREAKOUT_OVEREXTENDED"
    assert strat.evaluate(f, bars) is None
    assert strat.locked_box is None


def test_ternary_confirmation_scoring():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f, bars)
    advance(f, bars, close=104)
    f.spot_price = 104.0

    # Case A: 2 pass, 1 fail, 3 None -> 2 of 3 available (PASS)
    f.futures_price = 110.0
    f.futures_vwap = 115.0  # VWAP fails
    f.rvol_5m = 0.0         # None
    f.breakout_bull_derivatives_score = None  # None
    f.futures_buildup = None                 # None

    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["confirmation"]["score"] == 2
    assert diag.phase_summary["confirmation"]["available"] == 3
    assert diag.overall_status == "READY_TO_TRIGGER"
    assert strat.evaluate(f, bars) is not None

    # Case B: 1 pass, 2 fail, 3 None -> 1 of 3 available (FAIL: CONFIRMATION_SCORE_LOW)
    f2, bars2 = setup()
    strat2 = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat2.evaluate(f2, bars2)
    c_weak = bars2[-1].model_copy(update={
        "start_time": bars2[-1].end_time, "end_time": bars2[-1].end_time + timedelta(minutes=5),
        "open": 103.5, "high": 104.0, "low": 101.0, "close": 104.0,
    })
    bars2.append(c_weak)
    f2.timestamp = c_weak.end_time
    f2.spot_price = 104.0
    f2.futures_price = 110.0
    f2.futures_vwap = 115.0  # VWAP fails
    f2.rvol_5m = 0.0         # None
    f2.breakout_bull_derivatives_score = None  # None
    f2.futures_buildup = None                 # None

    diag2 = strat2.diagnose(f2, bars2)[0]
    assert diag2.phase_summary["confirmation"]["score"] == 1
    assert diag2.phase_summary["confirmation"]["available"] == 3
    assert diag2.phase_summary["primary_blocker"] == "CONFIRMATION_SCORE_LOW"

    # Case C: 2 pass, 0 fail, 4 None -> 2 of 2 available (FAIL: INSUFFICIENT_CONFIRMATION_DATA < 3)
    f3, bars3 = setup()
    strat3 = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat3.evaluate(f3, bars3)
    advance(f3, bars3, close=104)
    f3.spot_price = 104.0
    f3.futures_vwap = 0.0   # None
    f3.futures_price = 0.0  # None
    f3.rvol_5m = 0.0        # None
    f3.breakout_bull_derivatives_score = None  # None
    f3.futures_buildup = None                 # None

    diag3 = strat3.diagnose(f3, bars3)[0]
    assert diag3.phase_summary["confirmation"]["score"] == 2
    assert diag3.phase_summary["confirmation"]["available"] == 2
    assert diag3.phase_summary["primary_blocker"] == "INSUFFICIENT_CONFIRMATION_DATA"


def test_structural_risk_gate():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f, bars)
    advance(f, bars, close=104)

    # 1. Normal risk: live_price = 104.0. Stop = 103 - 1.25 = 101.75. Risk = 2.25 (0.45 ATR <= 1.20 ATR) -> PASS
    f.spot_price = 104.0
    diag_pass = strat.diagnose(f, bars)[0]
    assert diag_pass.phase_summary["risk"]["initial_risk_atr"] == 0.45
    assert diag_pass.overall_status == "READY_TO_TRIGGER"

    # 2. Risk too high: spot_price = 108.0 with max_extension relaxed to 1.5 ATR
    f_risk, bars_risk = setup()
    strat_risk = VolatilityBreakoutStrategy(breakout_confirm_polls=1, max_extension_atr=1.5)
    strat_risk.evaluate(f_risk, bars_risk)
    advance(f_risk, bars_risk, close=104)
    f_risk.spot_price = 108.0
    diag_risk = strat_risk.diagnose(f_risk, bars_risk)[0]
    assert diag_risk.phase_summary["primary_blocker"] == "RISK_TOO_HIGH"

