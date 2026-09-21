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

## Review-fix acceptance mapping (2026-09-21)

The phase headings remain `[?]` pending human approval. The following
concrete tests close the review gaps and are the authoritative additions to
the original matrix:

| Review rule | Concrete test |
|---|---|
| Futures entry/R normal and gap CALL/PUT | `tests/test_strategy_a_review_fixes.py::test_strategy_signal_uses_actual_futures_trigger_or_gap_entry_for_r` |
| Strategy A 15:15 and Strategy B 15:20 | `::test_strategy_a_forced_exit_uses_tunable_1515_boundary`; `::test_strategy_b_keeps_legacy_1520_force_exit_schedule` |
| Enforced monotonic +1R stop | `::test_strategy_a_protective_stop_is_enforced_and_monotonic` |
| Whole-lot T1 (1/2/3/4 lots) | `::test_strategy_a_t1_partial_exit_is_deterministic_whole_lots` |
| Explicit session and rollover reset | `::test_strategy_a_session_boundaries_are_explicit_and_strategy_b_remains_separate`; `::test_strategy_rollover_resets_runtime_state_with_explicit_reason` |
| Preferred delta rank and quote timestamp rejection | `::test_preferred_delta_band_is_ranked_before_outside_band_quality`; `::test_strategy_a_rejects_missing_naive_future_and_stale_quotes` |
| Holiday expiry semantics | `::test_expiry_holiday_configuration_changes_session_eligibility` |
| Phase 8 trend/confirmation/confluence boundary matrix | `::test_phase8_trend_regime_matrix`; `::test_phase8_confirmation_matrix`; `::test_phase8_confluence_and_structural_stop_use_confirmed_extremes_and_room` |
| Phase 9 state/rejection accounting | `::test_phase9_rejection_buckets_are_mutually_accounted`; `::test_phase9_requires_authoritative_underlying_entry_for_strategy_a` |
| Genuine runtime/replay parity | `::test_genuine_runtime_vs_replay_decision_parity_uses_separate_strategy_instances` |
| Telemetry comparison and restart persistence | `::test_telemetry_comparison_is_exact_and_restart_persistence_shape_is_structured`; `::test_strategy_a_telemetry_persists_and_restores_across_service_restart` |
