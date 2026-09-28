from services.historical.independent_options_intrabar_timing_findings import (
    DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1,
)


def test_intrabar_timing_result_remains_development_only():
    result = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["signal"]["definition_changed"] is False
    assert result["signal"]["threshold_selected"] is False


def test_intrabar_tape_exactly_reconciles_to_five_minute_development_futures():
    qa = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1["intrabar_qa"]
    assert qa["complete_375_bar_sessions"] == "80/80"
    assert qa["duplicate_rows"] == 0
    assert qa["invalid_ohlc_rows"] == 0
    assert qa["wrong_contract_rows"] == 0
    assert qa["failed_requests"] == 0
    assert qa["five_minute_bars_reconciled"] == 6000
    assert qa["exact_ohlcv_open_interest_reconciliation"] is True
    assert qa["exact_contract_reconciliation"] is True


def test_options_specific_lead_is_concentrated_in_first_minute():
    result = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1
    minutes = result["minute_open_to_close"]
    first = minutes["minute_1"]
    assert first["spearman"] > 0.15
    assert first["mean_direction_aligned_bps"] > 0.3
    assert first["positive_correlation_blocks"] == "8/8"
    assert first["positive_mean_blocks"] == "8/8"
    for minute in ("minute_2", "minute_3", "minute_4", "minute_5"):
        assert abs(minutes[minute]["spearman"]) < 0.01
        assert abs(minutes[minute]["mean_direction_aligned_bps"]) < 0.03


def test_one_minute_latency_removes_relationship_and_o3_is_not_frozen():
    result = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1
    latency = result["latency_challenge"]
    immediate = latency["entry_minute_1_open_to_minute_5_close"]
    delayed = latency["entry_minute_2_open_to_minute_5_close"]
    assert immediate["spearman"] > 0.09
    assert immediate["positive_correlation_blocks"] == "8/8"
    assert abs(delayed["spearman"]) < 0.01
    assert delayed["mean_direction_aligned_bps"] < 0.03
    assert result["status"] == "FAST_PRICE_DISCOVERY_BEHAVIOR_NOT_FROZEN_AS_O3"


def test_economic_screen_does_not_promote_development_quartiles():
    result = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1
    screen = result["economic_screen"]
    magnitude = result["magnitude_challenge"]
    assert max(magnitude["absolute_signal_quartile_minute_1_mean_aligned_bps"]) < 1.0
    assert screen["futures_stt_rate_from_2026_04_01_bps_on_sell_value"] == 5.0
    assert result["candidate_frozen"] is False
