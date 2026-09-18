# Strategy A implementation notes

## Implemented corrections

- Relaxed defaults: ADX 20, futures RVOL 1.20, two of six confirmations,
  normalized EMA slope threshold 0.10, entry window beginning at 09:20 IST.
  These remain configurable. Revision 2 migrates the previous RVOL 1.30 and
  09:30 defaults; non-default stored values are preserved.
- Shared signal/diagnostic evaluator for bullish and bearish setups. Persisted
  impulse state, completed-bar trigger deduplication, session resets and reset
  cutoffs prevent repeated or stale setup entries.
- Chronological impulse, 2–9 pullback bars excluding the trigger, 10–65% depth,
  aligned futures-volume confirmation, historical futures VWAP retest and prior
  breakout-level retest. Initial R uses the frozen trigger close and actual ATR,
  with the specified 0.45–1.60 ATR range.
- No simulated/unknown-source candles, synthetic futures or synthetic chains in
  strategy evaluation. Quotes now retain source provenance; quote-built candle
  volume uses cumulative-volume differences, not the sum of daily totals.
- Completed 5m bars drive invalidation, health scoring and trailing updates.
  Emergency stops retain quote-based evaluation. Trade management state survives
  repository round trips, including swing references, peak closes and order IDs.
- Contract selection uses stored instrument lot size, expiry and ID, actual
  bid/ask, and strike-distance ranking. No fabricated contract metadata fallback.
- Live entries/exits remain pending until OMS reconciliation. Entry premium and
  its emergency stop use the reported fill. Exits use executable bids, including
  a fresh exact-expiry chain lookup when no option subscription is available.
  An entry interrupted before its order ID is saved remains pending and requires
  operator reconciliation; it is not treated as filled or automatically retried.
- Daily loss/trade gates, configurable account equity and additional parameter
  controls are wired through the strategy service and UI.

## Runtime prerequisites and limitations

1. Import/verify the current broker instrument master, including a tradable NIFTY
   futures contract and current option expiries/lot sizes. The existing bootstrap
   seeder is not an authoritative instrument master. A read-only check during
   this implementation found two EQUITY rows, 270 OPTIONS rows and no FUTURES
   rows in the local database. No live master import was performed.
2. Provide an authenticated Breeze feed and sufficient completed spot/futures
   history (including 50 completed 15m spot bars for EMA50). Missing, stale or
   unaligned data intentionally blocks entries, even when overrides are relaxed.
3. Restart the backend to load the implementation and run repository migrations.
   Inspect saved settings and temporary overrides before paper validation.
4. Replay is explicitly **SIGNALS_ONLY** on real stored candles. Historical
   executable option quotes are not available here, so trades, fills and PnL are
   not estimated. Zero-valued legacy performance fields are not backtest results.
   Available replay dates exclude simulated history. Replay lacks historical
   option-chain confirmation inputs and is not a full live-execution reproduction.
5. Automated tests cover strategy boundaries, signal/diagnostic parity, source
   rejection, persistence, defaults migration and mocked fill reconciliation.
   They do not establish profitability or verify live broker execution. No live
   orders were placed as part of these updates.

## Verification commands

- `.venv/Scripts/python.exe -m pytest tests -q` (use an isolated working directory
  with the repository on PYTHONPATH to avoid tests writing application databases).
- `npx tsc --noEmit` from `frontend/trading-ui`.
- `git diff --check`.
