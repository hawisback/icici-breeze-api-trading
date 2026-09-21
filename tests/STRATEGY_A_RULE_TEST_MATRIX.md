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

## Final-hardening concrete coverage (2026-09-21)

| Rule / review finding | Concrete test |
|---|---|
| Missing option quote does not block structural decision; pending exit later fills original reason | `tests/test_strategy_a_hardening.py::test_strategy_a_structural_stop_is_decided_without_option_quote_and_later_bid_closes_original_reason` |
| Missing quote trailing and 15:15 decisions remain pending | `::test_strategy_a_trailing_stop_and_force_exit_are_pending_without_quote` |
| T1 decision/fill separation, whole-lot quantity, paper slippage, no repeat | `::test_strategy_a_t1_decision_without_quote_preserves_quantity_then_fills_once_with_slippage`; `test_strategy_a_review_fixes.py::test_strategy_a_t1_partial_exit_is_deterministic_whole_lots` |
| Weighted realized R, CALL/PUT symmetry, replay reuse | `tests/test_strategy_a_hardening.py::test_weighted_realized_r_is_canonical_and_call_put_symmetric`; `::test_replay_uses_the_same_weighted_r_helper_for_partial_outcomes` |
| Entry/T1/final transaction ledger and one-lot no-partial | `::test_partial_transaction_costs_use_three_execution_legs_and_actual_quantities`; `::test_one_lot_has_no_partial_order_and_realized_r_is_final_exit_r` |
| Canonical overlapping futures stream and one actual rollover | `::test_canonical_replay_stream_selects_one_contract_per_timestamp_and_one_real_rollover` |
| Trend DI, ADX threshold, EMA separation boundaries | `::test_phase8_trend_di_adx_and_ema_separation_exact_boundaries`; `test_strategy_a_review_fixes.py::test_phase8_trend_regime_matrix` |
| Confirmation body/close-location/zero-range/1.50 ATR boundaries | `::test_phase8_confirmation_exact_body_close_location_zero_range_and_range_boundaries`; `test_strategy_a_review_fixes.py::test_phase8_confirmation_matrix` |
| S/R, pivot/confluence and outside-zone cases | `::test_phase8_confluence_explicit_ema_vwap_sr_and_pivot_confirmation_cases`; `test_strategy_a_review_fixes.py::test_phase8_confluence_and_structural_stop_use_confirmed_extremes_and_room` |
| Structural-R 0.80/1.50 and room 1.50R boundaries; pivot confirmation; duplicate/session lifecycle | `::test_phase8_structural_r_and_opposing_room_exact_boundaries`; `::test_phase8_pivot_confirmation_trigger_duplicate_and_session_boundaries_are_explicit` |
| Lifecycle telemetry and full summary metrics | `::test_strategy_a_telemetry_summary_counts_selection_sizing_execution_and_discrepancies`; `test_strategy_a_review_fixes.py::test_telemetry_comparison_is_exact_and_restart_persistence_shape_is_structured` |
| Strategy A LIVE remains blocked | `test_strategy_a_review_fixes.py::test_live_safety_has_no_strategy_a_order_path_in_review_fixes`; `tests/test_live_gate.py` |
