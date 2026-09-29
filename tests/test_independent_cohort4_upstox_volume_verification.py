import copy

import pytest

import services.historical.independent_cohort4_upstox_volume_verification as mod


def _raw(interval_minutes: int):
    start = mod.datetime.fromisoformat(f"{mod.TARGET_DAY}T09:15:00+05:30")
    count = mod.EXPECTED_5M_ROWS if interval_minutes == 5 else mod.EXPECTED_1M_ROWS
    rows = []
    for index in range(count):
        ts = start + mod.timedelta(minutes=interval_minutes * index)
        base = 25000.0 + index * 0.1
        rows.append([
            ts.isoformat(),
            base,
            base + 0.4,
            base - 0.3,
            base + 0.1,
            1000.0 + index,
            10_000_000.0 + index,
        ])
    return rows


def _coherent_raw():
    one = _raw(1)
    five = []
    for i in range(0, len(one), 5):
        chunk = one[i:i+5]
        five.append([
            chunk[0][0],
            chunk[0][1],
            max(row[2] for row in chunk),
            min(row[3] for row in chunk),
            chunk[-1][4],
            sum(row[5] for row in chunk),
            chunk[-1][6],
        ])

    target_index = 63  # 09:15 + 63*5m = 14:30
    target_one_start = target_index * 5
    desired = [160000.0, 165000.0, 170000.0, 166000.0, 165950.0]
    for offset, volume in enumerate(desired):
        one[target_one_start + offset][5] = volume

    chunk = one[target_one_start:target_one_start + 5]
    five[target_index][1] = mod.BREEZE_TARGET_OHLC["open"]
    five[target_index][2] = mod.BREEZE_TARGET_OHLC["high"]
    five[target_index][3] = mod.BREEZE_TARGET_OHLC["low"]
    five[target_index][4] = mod.BREEZE_TARGET_OHLC["close"]
    five[target_index][5] = sum(desired)

    # Force target 1m bars to aggregate exactly to Breeze/Upstox 5m OHLC.
    target_prices = [
        [25733.9, 25736.0, 25733.9, 25735.0],
        [25735.0, 25738.0, 25734.5, 25737.0],
        [25737.0, 25740.0, 25736.0, 25738.0],
        [25738.0, 25739.0, 25735.0, 25736.0],
        [25736.0, 25737.0, 25734.0, 25734.5],
    ]
    for offset, prices in enumerate(target_prices):
        row = one[target_one_start + offset]
        row[1:5] = prices

    chunk = one[target_one_start:target_one_start + 5]
    five[target_index] = [
        chunk[0][0],
        chunk[0][1],
        max(row[2] for row in chunk),
        min(row[3] for row in chunk),
        chunk[-1][4],
        sum(row[5] for row in chunk),
        chunk[-1][6],
    ]
    assert five[target_index][1] == mod.BREEZE_TARGET_OHLC["open"]
    assert five[target_index][2] == mod.BREEZE_TARGET_OHLC["high"]
    assert five[target_index][3] == mod.BREEZE_TARGET_OHLC["low"]
    assert five[target_index][4] == mod.BREEZE_TARGET_OHLC["close"]
    return five, one


def _contract():
    return {
        "name": "NIFTY",
        "segment": "NSE_FO",
        "exchange": "NSE",
        "expiry": mod.TARGET_EXPIRY,
        "instrument_key": "NSE_FO|99999|31-07-2025",
        "trading_symbol": "NIFTY FUT 31 JUL 25",
        "instrument_type": "FUT",
        "underlying_key": mod.UNDERLYING_KEY,
        "underlying_symbol": "NIFTY",
    }


def test_verification_is_frozen_to_exact_input_and_target():
    assert mod.INPUT_SHA256 == (
        "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
    )
    assert mod.UNDERLYING_KEY == "NSE_INDEX|Nifty 50"
    assert mod.TARGET_EXPIRY == "2025-07-31"
    assert mod.TARGET_DAY == "2025-06-27"
    assert mod.TARGET_TIMESTAMP == "2025-06-27T14:30:00+05:30"


def test_independent_provider_evidence_passes_only_when_1m_and_5m_agree():
    raw_5m, raw_1m = _coherent_raw()
    report = mod.verify(
        input_sha256=mod.INPUT_SHA256,
        contract=_contract(),
        raw_5m=raw_5m,
        raw_1m=raw_1m,
    )

    assert report["decision"] == "INDEPENDENT_PROVIDER_EVIDENCE_PASSED"
    assert report["target"]["target_ohlc_matches_breeze"] is True
    assert report["target"]["upstox_1m_matches_upstox_5m"] is True
    assert report["target"]["upstox_5m"]["volume"] == 826950.0
    assert report["target"]["upstox_1m_aggregate"]["volume"] == 826950.0
    assert report["guardrails"]["artifact_modified"] is False
    assert report["guardrails"]["correction_authorized"] is False


def test_verification_rejects_upstox_target_ohlc_disagreement():
    raw_5m, raw_1m = _coherent_raw()
    raw_5m[63][4] += 0.1

    with pytest.raises(ValueError, match="does not match Breeze"):
        mod.verify(
            input_sha256=mod.INPUT_SHA256,
            contract=_contract(),
            raw_5m=raw_5m,
            raw_1m=raw_1m,
        )


def test_verification_rejects_upstox_1m_volume_disagreement():
    raw_5m, raw_1m = _coherent_raw()
    raw_1m[63 * 5][5] += 1.0

    with pytest.raises(ValueError, match="aggregate volume"):
        mod.verify(
            input_sha256=mod.INPUT_SHA256,
            contract=_contract(),
            raw_5m=raw_5m,
            raw_1m=raw_1m,
        )


def test_verification_rejects_nonpositive_upstox_volume():
    raw_5m, raw_1m = _coherent_raw()
    raw_1m[63 * 5][5] = -1.0

    with pytest.raises(ValueError, match="nonpositive volume"):
        mod.verify(
            input_sha256=mod.INPUT_SHA256,
            contract=_contract(),
            raw_5m=raw_5m,
            raw_1m=raw_1m,
        )
