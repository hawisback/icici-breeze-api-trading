# Strategy A rule-to-test matrix

| Rule | Automated coverage |
|---|---|
| Frozen setup/snapshot, strict fields, timezone-aware timestamps | `test_strategy_a_v2.py::test_strategy_contracts_are_frozen_and_entered_consumes_setup`; `test_strategy_contracts.py` |
| EMA20/EMA50, ADX14, DI, ATR14, session VWAP | `FuturesFeatureEngine` tests in `test_strategy_a_v2.py`; feature parity via replay tests |
| Completed 15m bars and exact 3×5m aggregation | `test_aggregation_requires_three_contiguous_completed_futures_bars` |
| No future feature/pivot look-ahead | `test_future_bars_cannot_change_as_of_features_or_pivots` |
| Single-contract futures resolution/no splicing | `test_contract_resolver_never_splices_futures_contracts` |
| Confirmed two-left/two-right pivots | `FuturesFeatureEngine.confirmed_pivots`; replay and feature tests |
| Trend, confluence, confirmation and structural-R boundaries | `TrendPullbackStrategy` focused acceptance suite; boundary fixtures are generated from the authoritative config |
| FLAT → SETUP → ARMED → ENTERED → COOLDOWN and duplicate protection | `test_strategy_contracts.py`; state machine implementation tests |
| Expiry and two trading-session option expiry rule | `test_expiry_requires_two_trading_sessions_not_two_calendar_days` |
| Delta provenance/ranking, freshness, spread and liquidity | `test_option_selector_requires_delta_and_ranks_inside_preferred_band` |
| Structural futures-R sizing and configured fallback behavior | `test_structural_r_sizing_rejects_missing_delta_without_fallback` |
| Production-path replay identity and event-level comparison | `test_replay_report_is_versioned_and_comparison_is_event_level` |
| Forward option validation state separation | `test_forward_option_validation_keeps_underlying_and_option_states_separate` |
| Structured telemetry summary and runtime/replay comparison | `services.strategy.telemetry` unit surface; service integration smoke coverage |
| Strategy B/shared safety regressions | `test_strategy_b_forward_validation.py`, `test_strategy_b_replay_lifecycle.py`, `test_volatility_breakout_fixed.py`, shared service/OMS/risk suites |
