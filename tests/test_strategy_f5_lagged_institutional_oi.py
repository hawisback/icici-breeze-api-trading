from datetime import date

from services.historical.strategy_f5_lagged_institutional_oi import (
    _change_state,
    _context_by_session,
    _participant_metrics,
    _position_state,
)
from services.historical.strategy_f5_lagged_institutional_oi_market import (
    _parse_participant_oi_csv,
)
from services.historical.strategy_f5_lagged_institutional_oi_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def _csv_text():
    return """Participant wise Open Interest (no. of contracts) in equity derivatives as on Jul 01, 2026
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts,Total Short Contracts
Client,100,80,1,2,10,20,30,40,3,4,5,6,138,163
DII,20,40,1,2,5,6,7,8,3,4,5,6,39,68
FII,70,100,1,2,11,12,13,14,3,4,5,6,101,140
Pro,30,20,1,2,7,8,9,10,3,4,5,6,53,52
TOTAL,220,240,4,8,33,46,59,72,12,16,20,24,331,423
"""


def test_protocol_is_lagged_and_no_threshold_search():
    assert PROTOCOL_VERSION == "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_V1"
    assert GUARDRAILS["context_is_lagged_one_session"] is True
    assert GUARDRAILS["no_same_day_participant_report_backfill"] is True
    assert GUARDRAILS["no_position_magnitude_threshold_search"] is True
    assert GUARDRAILS["live_execution"] is False


def test_parse_official_participant_oi_shape():
    rows = _parse_participant_oi_csv(
        _csv_text(),
        report_date=date(2026, 7, 1),
        source_url="https://example.invalid/report.csv",
    )
    assert {r["participant"] for r in rows} == {
        "Client", "DII", "FII", "Pro"
    }
    fii = next(r for r in rows if r["participant"] == "FII")
    assert fii["future_index_long"] == 70
    assert fii["future_index_short"] == 100
    assert fii["option_index_put_short"] == 14


def test_position_and_change_states_have_no_magnitude_threshold():
    assert _position_state(1) == "NET_LONG"
    assert _position_state(-1) == "NET_SHORT"
    assert _position_state(0) == "FLAT"
    assert _change_state(1) == "MORE_LONG"
    assert _change_state(-1) == "MORE_SHORT"
    assert _change_state(0) == "FLAT"


def test_participant_metrics_option_balance_is_only_proxy():
    row = next(
        r
        for r in _parse_participant_oi_csv(
            _csv_text(),
            report_date=date(2026, 7, 1),
            source_url="https://example.invalid/report.csv",
        )
        if r["participant"] == "FII"
    )
    metrics = _participant_metrics(row)
    assert metrics["index_futures_net"] == -30
    assert metrics["index_futures_state"] == "NET_SHORT"
    assert metrics["index_options_directional_balance"] == 0
    assert metrics["index_options_directional_balance_note"] == (
        "DESCRIPTIVE_PROXY_NOT_DELTA_OR_GAMMA"
    )


def test_session_context_uses_prior_report_not_same_day():
    sessions = [
        date(2026, 6, 29),
        date(2026, 6, 30),
        date(2026, 7, 1),
    ]
    rows = []
    for report_day, fii_long, fii_short in [
        (date(2026, 6, 29), 80, 100),
        (date(2026, 6, 30), 70, 110),
    ]:
        for participant in ("FII", "DII"):
            rows.append({
                "date": report_day.isoformat(),
                "participant": participant,
                "future_index_long": (
                    fii_long if participant == "FII" else 20
                ),
                "future_index_short": (
                    fii_short if participant == "FII" else 40
                ),
                "option_index_call_long": 10,
                "option_index_put_long": 20,
                "option_index_call_short": 30,
                "option_index_put_short": 40,
            })
    context = _context_by_session(sessions, rows)["2026-07-01"]
    assert context["prior_report_date"] == "2026-06-30"
    assert context["prior2_report_date"] == "2026-06-29"
    assert context["FII"]["index_futures_net"] == -40
    assert context["FII"]["index_futures_change_state"] == "MORE_SHORT"
