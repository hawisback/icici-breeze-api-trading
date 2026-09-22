from services.historical.strategy_cd_guard_validation import (
    _max_drawdown,
    _metrics,
    build_report,
)


def _trade(
    *,
    day: str,
    realized_r: float,
    delay: float,
    risk_atr: float,
    separation: float,
    direction: str = "CALL",
) -> dict:
    return {
        "date": day,
        "direction": direction,
        "entry_time": f"{day}T04:31:00+00:00",
        "entry_time_ist": f"{day}T10:01:00+05:30",
        "realized_r": realized_r,
        "mfe_r": max(0.0, realized_r),
        "mae_r": 1.0 if realized_r < 0 else 0.25,
        "research_features": {
            "trigger_delay_minutes": delay,
            "risk_atr": risk_atr,
            "context_15m_ema_separation_atr": separation,
        },
    }


def test_max_drawdown_is_path_ordered():
    assert _max_drawdown([1.0, -1.0, -1.0, 2.0]) == -2.0


def test_metrics_report_frequency_and_profit_factor():
    rows = [
        _trade(day="2026-01-01", realized_r=2.0, delay=1.0, risk_atr=1.0, separation=1.0),
        _trade(day="2026-01-02", realized_r=-1.0, delay=1.0, risk_atr=1.0, separation=1.0),
        _trade(day="2026-01-03", realized_r=0.5, delay=1.0, risk_atr=1.0, separation=1.0),
    ]
    result = _metrics(rows, usable_sessions=10)
    assert result["trades"] == 3
    assert result["trades_per_session"] == 0.3
    assert result["mean_r"] == 0.5
    assert result["total_r"] == 1.5
    assert result["profit_factor"] == 2.5
    assert result["max_drawdown_r"] == -1.0


def test_focal_di_guard_requires_fast_trigger_risk_floor_and_separation_cap():
    dates = [f"2025-01-{day:02d}" for day in range(1, 29)]
    discovery = {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "usable_sessions": len(dates),
        "usable_dates": dates,
        "strategy_a_v3_signal_dates": ["2025-01-02"],
        "families": {
            "di_continuation": {
                "trades": [
                    _trade(day="2025-01-01", realized_r=2.0, delay=2.0, risk_atr=0.90, separation=1.80),
                    _trade(day="2025-01-02", realized_r=-1.0, delay=3.0, risk_atr=1.00, separation=1.00),
                    _trade(day="2025-01-03", realized_r=-1.0, delay=1.0, risk_atr=0.80, separation=1.00),
                    _trade(day="2025-01-04", realized_r=-1.0, delay=1.0, risk_atr=1.00, separation=2.10),
                ]
            },
            "controlled_vwap_mean_reversion": {"trades": []},
        },
    }

    report = build_report(discovery)
    focal = report["candidates"]["di_fast_risk090_sep180"]
    assert focal["all_sessions"]["trades"] == 1
    assert focal["all_sessions"]["total_r"] == 2.0
    assert focal["strategy_a_v3_same_day_overlap"]["trades_on_v3_signal_days"] == 0


def test_robustness_grid_is_deliberately_coarse():
    discovery = {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "usable_sessions": 1,
        "usable_dates": ["2026-01-01"],
        "families": {
            "di_continuation": {"trades": []},
            "controlled_vwap_mean_reversion": {"trades": []},
        },
    }
    report = build_report(discovery)
    grid = report["di_robustness_grid"]
    assert len(grid) == 9
    assert {row["risk_floor_atr"] for row in grid} == {0.85, 0.90, 0.95}
    assert {row["context_15m_ema_separation_cap_atr"] for row in grid} == {1.5, 1.8, 2.0}
