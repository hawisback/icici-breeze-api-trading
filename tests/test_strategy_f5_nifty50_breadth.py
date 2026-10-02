from datetime import date

from services.historical.strategy_f5_nifty50_breadth import (
    _breadth_day,
)
from services.historical.strategy_f5_nifty50_breadth_market import (
    _extract_isec_stock_code,
    _membership,
)
from services.historical.strategy_f5_nifty50_breadth_protocol import (
    BREADTH_DEFINITION,
    GUARDRAILS,
    NIFTY50_JUNE_2026,
    PROTOCOL_VERSION,
    SEPTEMBER_30_CHANGE,
)


def test_breadth_protocol_freezes_historical_membership_and_no_threshold():
    assert PROTOCOL_VERSION == "STRATEGY_F5_NIFTY50_BREADTH_V1"
    assert len(NIFTY50_JUNE_2026) == 50
    assert SEPTEMBER_30_CHANGE == {
        "effective_date": "2026-09-30",
        "exclude": "WIPRO",
        "include": "BSE",
    }
    assert BREADTH_DEFINITION["magnitude_threshold"] is None
    assert GUARDRAILS["no_breadth_strength_threshold_search"] is True
    assert GUARDRAILS["no_futures_state_combination_in_this_pass"] is True


def test_membership_replaces_wipro_with_bse_on_sep30():
    before = _membership(date(2026, 9, 29))
    after = _membership(date(2026, 9, 30))
    assert len(before) == 50
    assert len(after) == 50
    assert "WIPRO" in before and "BSE" not in before
    assert "BSE" in after and "WIPRO" not in after


def test_extract_isec_stock_code_handles_direct_and_nested_payloads():
    assert _extract_isec_stock_code({"isec_stock_code": "RELIND"}) == "RELIND"
    assert _extract_isec_stock_code(
        {"Success": {"isec_stock_code": "ICIBAN"}}
    ) == "ICIBAN"
    assert _extract_isec_stock_code(
        {"Success": [{"isec_stock_code": "TATMOT"}]}
    ) == "TATMOT"


def test_breadth_day_uses_simple_majority_with_complete_coverage():
    day = "2026-07-01"
    members = _membership(date.fromisoformat(day))
    rows = []
    for i, symbol in enumerate(members):
        start = 100.0
        end = 101.0 if i < 30 else 99.0
        rows.extend([
            {
                "date": day,
                "time": "09:15",
                "nse_symbol": symbol,
                "open": start,
            },
            {
                "date": day,
                "time": "15:20",
                "nse_symbol": symbol,
                "open": end,
            },
        ])
    result = _breadth_day(day, rows)
    assert result["available"] is True
    assert result["advancers"] == 30
    assert result["decliners"] == 20
    assert result["breadth_direction"] == "BULLISH"
    assert result["coverage_pct"] == 100.0
