"""Frozen Phase-2 NIFTY futures confirmation diagnostic for F5.

Purpose
-------
Add front-month NIFTY futures activity as an independent context layer after
the dominant NIFTY regime finding.

This remains a whole-day DEVELOPMENT diagnostic. It does not yet create a
same-day trading filter.

Futures contract
----------------
Use the nearest non-expired monthly NIFTY future for each Jul-Sep 2026 session.
Frozen monthly expiries:
- 2026-07-28
- 2026-08-25
- 2026-09-29
- 2026-10-27

Futures state
-------------
Using 09:15 OPEN -> 15:20 OPEN futures price direction and 09:15 -> 15:20
open-interest direction:

- LONG_BUILDUP: price up, OI up
- SHORT_BUILDUP: price down, OI up
- SHORT_COVERING: price up, OI down
- LONG_UNWINDING: price down, OI down
- FLAT_OR_MISSING otherwise

Also report:
- opening futures basis = futures 09:15 open - spot 09:15 open
- 15:20 futures basis = futures 15:20 open - spot 15:20 open
- basis change
- summed 5-minute futures volume through 15:20

Primary questions
-----------------
1. On BEARISH dominant-NIFTY days, does PE perform especially well when
   futures state is SHORT_BUILDUP?
2. Does bearish CE remain weak under SHORT_BUILDUP?
3. Is the effect consistent by month?
4. On BULLISH days, does LONG_BUILDUP improve CE?
5. Are futures states useful beyond the dominant spot-regime label?

No numeric threshold search is allowed.
"""

PROTOCOL_VERSION = "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_V1"
STRATEGY_ID = "F5"
ROLE = "NIFTY_FUTURES_CONTEXT_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
    "session_start": "09:15",
    "context_end": "15:20",
    "interval": "5minute",
}

MONTHLY_FUTURES_EXPIRIES = [
    "2026-07-28",
    "2026-08-25",
    "2026-09-29",
    "2026-10-27",
]

FUTURES_STATE = {
    "LONG_BUILDUP": "PRICE_UP_AND_OI_UP",
    "SHORT_BUILDUP": "PRICE_DOWN_AND_OI_UP",
    "SHORT_COVERING": "PRICE_UP_AND_OI_DOWN",
    "LONG_UNWINDING": "PRICE_DOWN_AND_OI_DOWN",
    "FLAT_OR_MISSING": "ANY_ZERO_OR_MISSING_DIRECTION",
}

STRICT_BUILD_CONFIRMATION = {
    "BULLISH": "LONG_BUILDUP",
    "BEARISH": "SHORT_BUILDUP",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "breeze_only_broker_api": True,
    "whole_day_context_uses_future_information": True,
    "do_not_use_as_same_day_entry_filter": True,
    "matched_baseline_trades_only": True,
    "no_filtered_path_resimulation": True,
    "no_oi_threshold_search": True,
    "no_basis_threshold_search": True,
    "no_volume_threshold_search": True,
    "no_side_filter_promotion": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
