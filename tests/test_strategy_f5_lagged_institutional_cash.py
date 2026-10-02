from datetime import date

from services.historical.strategy_f5_lagged_institutional_cash import (
    _context_by_session,
    _flow_state,
    _offset_state,
)
from services.historical.strategy_f5_lagged_institutional_cash_market import (
    _normalize_payload,
    collect,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_lagged_institutional_cash_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def test_protocol_is_lagged_missing_safe_and_has_no_threshold_search():
    assert PROTOCOL_VERSION == "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_V1"
    assert GUARDRAILS["context_is_lagged_one_session"] is True
    assert GUARDRAILS["no_same_day_cash_report_backfill"] is True
    assert GUARDRAILS["missing_cash_data_separate_from_sign"] is True
    assert GUARDRAILS["no_cash_magnitude_threshold_search"] is True
    assert GUARDRAILS["live_execution"] is False


def test_normalize_generic_nse_category_rows():
    rows = _normalize_payload([
        {
            "category": "FII/FPI",
            "date": "01-Jul-2026",
            "buyValue": "12,000.5",
            "sellValue": "13,000.5",
            "netValue": "-1000",
        },
        {
            "category": "DII",
            "date": "01-Jul-2026",
            "buyValue": "8000",
            "sellValue": "7000",
            "netValue": "1000",
        },
    ])
    assert rows == [{
        "date": "2026-07-01",
        "FII_buy_cr": 12000.5,
        "FII_sell_cr": 13000.5,
        "FII_net_cr": -1000.0,
        "DII_buy_cr": 8000.0,
        "DII_sell_cr": 7000.0,
        "DII_net_cr": 1000.0,
    }]


def test_normalize_nse_category_footnote_markers():
    rows = _normalize_payload([
        {
            "category": "FII/FPI *",
            "date": "01-Jul-2026",
            "buyValue": 10,
            "sellValue": 12,
            "netValue": -2,
        },
        {
            "category": "DII **",
            "date": "01-Jul-2026",
            "buyValue": 14,
            "sellValue": 11,
            "netValue": 3,
        },
    ])
    assert rows[0]["FII_net_cr"] == -2.0
    assert rows[0]["DII_net_cr"] == 3.0


def test_normalize_wide_snapshot_shape():
    rows = _normalize_payload([{
        "date": "02-Jul-2026",
        "fiibuy": 10,
        "fiisell": 15,
        "fiinet": -5,
        "diibuy": 20,
        "diisell": 13,
        "diinet": 7,
    }])
    assert rows[0]["date"] == "2026-07-02"
    assert rows[0]["FII_net_cr"] == -5.0
    assert rows[0]["DII_net_cr"] == 7.0


def test_normalize_net_only_archived_rows():
    rows = _normalize_payload([{
        "date": "30-Jun-2026",
        "FII_net_cr": -2557,
        "DII_net_cr": 6842,
    }])
    assert rows == [{
        "date": "2026-06-30",
        "FII_buy_cr": None,
        "FII_sell_cr": None,
        "FII_net_cr": -2557.0,
        "DII_buy_cr": None,
        "DII_sell_cr": None,
        "DII_net_cr": 6842.0,
    }]


def test_flow_and_offset_states_use_sign_only():
    assert _flow_state(0.01) == "BUYING"
    assert _flow_state(-0.01) == "SELLING"
    assert _flow_state(0.0) == "FLAT"
    assert _offset_state("SELLING", "BUYING") == "FII_SELL_DII_BUY"
    assert _offset_state("BUYING", "SELLING") == "FII_BUY_DII_SELL"
    assert _offset_state("FLAT", "BUYING") == "OTHER"


def test_session_context_uses_prior_trading_session_not_same_day():
    sessions = [
        date(2026, 6, 30),
        date(2026, 7, 1),
        date(2026, 7, 2),
    ]
    rows = [
        {
            "date": "2026-06-30",
            "FII_buy_cr": 10.0,
            "FII_sell_cr": 12.0,
            "FII_net_cr": -2.0,
            "DII_buy_cr": 14.0,
            "DII_sell_cr": 11.0,
            "DII_net_cr": 3.0,
        },
        {
            "date": "2026-07-01",
            "FII_buy_cr": 30.0,
            "FII_sell_cr": 10.0,
            "FII_net_cr": 20.0,
            "DII_buy_cr": 10.0,
            "DII_sell_cr": 16.0,
            "DII_net_cr": -6.0,
        },
    ]
    context = _context_by_session(sessions, rows)
    jul1 = context["2026-07-01"]
    assert jul1["prior_report_date"] == "2026-06-30"
    assert jul1["FII"]["flow_state"] == "SELLING"
    assert jul1["offset_state"] == "FII_SELL_DII_BUY"
    jul2 = context["2026-07-02"]
    assert jul2["prior_report_date"] == "2026-07-01"
    assert jul2["FII"]["flow_state"] == "BUYING"


def test_missing_prior_report_stays_unavailable_not_a_sign_state():
    sessions = [date(2026, 6, 30), date(2026, 7, 1)]
    context = _context_by_session(sessions, [])
    jul1 = context["2026-07-01"]
    assert jul1["available"] is False
    assert jul1["reason"] == "PRIOR_CASH_REPORT_MISSING"
    assert "FII" not in jul1


def test_collector_requests_only_reports_that_can_be_lagged_into_target_days():
    f5_market = {
        "protocol_version": F5_PROTOCOL_VERSION,
        "strategy_outcomes_scored": False,
        "spot_rows": [
            {"date": "2026-06-30"},
            {"date": "2026-07-01"},
            {"date": "2026-07-02"},
        ],
    }
    source_rows = [
        {
            "date": day,
            "FII_buy_cr": 1.0,
            "FII_sell_cr": 2.0,
            "FII_net_cr": -1.0,
            "DII_buy_cr": 2.0,
            "DII_sell_cr": 1.0,
            "DII_net_cr": 1.0,
        }
        for day in ("2026-06-30", "2026-07-01", "2026-07-02")
    ]
    report = collect(
        f5_market,
        f5_market_sha256="f5",
        source_rows=source_rows,
        source_description="TEST_ARCHIVE",
        source_sha256="archive-sha",
    )
    assert report["quality"]["target_sessions"] == 2
    assert report["quality"]["needed_source_sessions"] == 2
    assert [r["date"] for r in report["cash_rows"]] == [
        "2026-06-30", "2026-07-01"
    ]
    assert report["source"]["source_file_sha256"] == "archive-sha"
