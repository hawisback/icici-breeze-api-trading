from datetime import date

from services.historical.independent_options_market_research import (
    BreezeOptionsClient,
    _atm_strike,
    _nearest_expiry_for_day,
    _selection_plan,
    _strike_band,
    build_options_dataset,
)


def _underlying(ts: str, close: float):
    return {"timestamp": ts, "futures_close": close}


def test_atm_rounding_is_deterministic_half_up():
    assert _atm_strike(23674.9, 50) == 23650
    assert _atm_strike(23675.0, 50) == 23700
    assert _atm_strike(23724.9, 50) == 23700


def test_strike_band_includes_atm_and_equal_radius():
    assert _strike_band(23680, 50, 2) == [23600, 23650, 23700, 23750, 23800]


def test_nearest_expiry_uses_same_day_then_next_supplied_expiry():
    expiries = [date(2026, 5, 19), date(2026, 5, 26)]
    assert _nearest_expiry_for_day(date(2026, 5, 19), expiries) == date(2026, 5, 19)
    assert _nearest_expiry_for_day(date(2026, 5, 20), expiries) == date(2026, 5, 26)


def test_selection_plan_preserves_contract_identity_and_unions_strikes():
    rows = [
        _underlying("2026-05-19T09:15:00+05:30", 23680),
        _underlying("2026-05-19T09:20:00+05:30", 23730),
        _underlying("2026-05-20T09:15:00+05:30", 23810),
    ]
    plan, strikes, dates = _selection_plan(
        rows,
        [date(2026, 5, 19), date(2026, 5, 26)],
        strike_step=50,
        strike_radius=1,
    )
    assert plan == {"2026-05-19": "2026-05-19", "2026-05-20": "2026-05-26"}
    assert strikes["2026-05-19"] == [23650, 23700, 23750, 23800]
    assert strikes["2026-05-26"] == [23750, 23800, 23850]
    assert dates["2026-05-19"] == ["2026-05-19"]
    assert dates["2026-05-26"] == ["2026-05-20"]


class FakeOptionClient:
    source = "BREEZE"

    def history(self, session_dates, expiry_date, strike_price, right):
        rows = []
        for day in session_dates:
            rows.append(
                {
                    "timestamp": f"{day.isoformat()}T09:15:00+05:30",
                    "expiry": expiry_date,
                    "strike": strike_price,
                    "right": "CE" if right == "call" else "PE",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000.0,
                    "open_interest": 5000.0,
                    "instrument": f"NIFTY {expiry_date} {strike_price} {right}",
                    "source": "BREEZE",
                }
            )
        return rows


def test_build_options_dataset_is_raw_and_does_not_stitch_contracts():
    payload = {
        "canonical_market_rows": [
            _underlying("2026-05-19T09:15:00+05:30", 23680),
            _underlying("2026-05-20T09:15:00+05:30", 23810),
        ]
    }
    report = build_options_dataset(
        payload,
        ["2026-05-19", "2026-05-26"],
        client=FakeOptionClient(),
        strike_step=50,
        strike_radius=1,
    )
    assert report["selection_policy"]["contract_stitching"] is False
    assert report["selection_policy"]["indicators_computed"] is False
    assert {row["expiry"] for row in report["option_candles"]} == {"2026-05-19", "2026-05-26"}
    assert report["quality"]["atm_ce_pe_pair_coverage_pct"] == 100.0


def test_breeze_options_client_uses_options_call_put_and_explicit_strike():
    calls = []

    class FakeSdk:
        def generate_session(self, api_secret, session_token):
            pass

        def get_historical_data_v2(self, **kwargs):
            calls.append(kwargs)
            return {
                "Status": 200,
                "Error": None,
                "Success": [
                    {
                        "datetime": "2026-05-20 09:15:00",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100.5,
                        "volume": 1234,
                        "open_interest": 5678,
                    }
                ],
            }

    client = BreezeOptionsClient("k", "s", "t", breeze_factory=lambda api_key: FakeSdk())
    rows = client.history([date(2026, 5, 20)], "2026-05-26", 23800, "call")
    assert len(rows) == 1
    assert rows[0]["right"] == "CE"
    assert rows[0]["strike"] == 23800
    assert rows[0]["volume"] == 1234
    assert rows[0]["open_interest"] == 5678
    assert calls[0]["product_type"] == "options"
    assert calls[0]["expiry_date"] == "2026-05-26T07:00:00.000Z"
    assert calls[0]["right"] == "call"
    assert calls[0]["strike_price"] == "23800"
