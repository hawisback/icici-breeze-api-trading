from datetime import datetime, timedelta

from services.historical.strategy_f5_canonical_options_indicators import (
    _canonical_frame,
    _oi_state_report,
)
from services.historical.strategy_f5_canonical_options_indicators_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    OI_STATES,
    PROTOCOL_VERSION,
)


def _bars(n=50):
    start = datetime.fromisoformat("2026-07-01T09:15:00+05:30")
    bars = []
    close = 100.0
    oi = 10000.0
    for i in range(n):
        open_ = close
        if i % 4 in (1, 2):
            close += 1.5
            oi += 120.0
        elif i % 4 == 3:
            close += 0.5
            oi -= 80.0
        else:
            close -= 0.8
            oi += 100.0
        bars.append({
            "timestamp": (start + timedelta(minutes=2 * i)).isoformat(),
            "date": "2026-07-01",
            "expiry": "2026-07-07",
            "strike": 25000,
            "right": "CE",
            "open": open_,
            "high": max(open_, close) + 0.5,
            "low": min(open_, close) - 0.5,
            "close": close,
            "volume": 500 + i * 10,
            "open_interest": oi,
        })
    return bars


def test_protocol_is_options_focused_and_nonexecution():
    assert PROTOCOL_VERSION == "STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_V1"
    assert "oi_change1_pct" in CONTINUOUS_FEATURES
    assert "volume_to_oi" in CONTINUOUS_FEATURES
    assert "atr14_pct" in CONTINUOUS_FEATURES
    assert "macd_hist_pct" in CONTINUOUS_FEATURES
    assert "LONG_BUILDUP" in OI_STATES
    assert GUARDRAILS["no_iv_or_greeks_without_explicit_data_or_model_protocol"]
    assert GUARDRAILS["live_execution"] is False


def test_canonical_frame_builds_oi_metrics_and_states():
    frame = _canonical_frame(_bars())
    tail = frame.iloc[-1]
    assert tail["oi_change1_pct"] == tail["oi_change1_pct"]
    assert tail["oi_change3_pct"] == tail["oi_change3_pct"]
    assert tail["oi_to_prior20_median"] == tail["oi_to_prior20_median"]
    assert tail["volume_to_oi"] == tail["volume_to_oi"]
    assert set(frame["oi_state"]).issubset(set(OI_STATES))
    assert "LONG_BUILDUP" in set(frame["oi_state"])
    assert "SHORT_COVERING" in set(frame["oi_state"])
    assert "SHORT_BUILDUP" in set(frame["oi_state"])


def test_oi_state_report_keeps_ce_pe_and_month_slices():
    rows = [
        {
            "oi_state": "LONG_BUILDUP",
            "month": "2026-07",
            "right": "CE",
            "bad_trade": False,
            "trail_activated": True,
            "baseline_winner": True,
            "baseline_net_pnl_inr": 100.0,
        },
        {
            "oi_state": "LONG_BUILDUP",
            "month": "2026-07",
            "right": "PE",
            "bad_trade": True,
            "trail_activated": False,
            "baseline_winner": False,
            "baseline_net_pnl_inr": -50.0,
        },
    ]
    report = _oi_state_report(rows)
    assert report["LONG_BUILDUP"]["overall"]["trades"] == 2
    assert report["LONG_BUILDUP"]["by_side"]["CE"]["trail_activation_rate_pct"] == 100.0
    assert report["LONG_BUILDUP"]["by_side"]["PE"]["bad_trade_rate_pct"] == 100.0
