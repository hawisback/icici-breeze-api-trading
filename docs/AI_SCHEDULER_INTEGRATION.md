# AI scheduler integration contract

This document describes the read-only contract for a one-minute NIFTY signal assistant.
It is **not** an autonomous order-execution API. External schedulers must run their
own orchestration and must not treat AI output as broker authority.

## Authentication and deployment

All `/api/v1/ai/*` endpoints now require an authorized identity: a valid
Bearer JWT, or the explicitly configured loopback-only local single-user mode.
Do not put broker credentials, access tokens, account numbers, or secrets into
LLM prompts or decision logs. Run the API on the loopback interface and use a
private service identity for a separately hosted scheduler.

## Scheduler cycle

1. Once per minute during the configured Indian market trading window,
   fetch **one** `GET /api/v1/ai/nifty/snapshot` and retain its `snapshot_id`.
   The snapshot deliberately uses the same ten-strike window as the default
   `/nifty/options` PCR scope.
2. Reject new entries whenever `entry_permitted` is not exactly `true`.
   Log all `blocking_reasons` and emit NO_TRADE. Missing, unavailable,
   simulated, delayed or unreconciled data is not a trading signal.
3. Use `technicals["1m"].metrics` for the latest *completed-bar*
   `macd_crossed_above_zero`, `macd_crossed_below_zero`,
   `macd_histogram_expanding_positive`,
   `macd_histogram_expanding_negative`, `rsi_crossed_above_55`,
   and `rsi_crossed_below_45` fields. Inspect `bar_end_time`,
   `macd_histogram_prev`, `rsi_14_prev` and `data_through`.
   Never infer a crossover merely from a current RSI/MACD threshold.
4. Use `technicals["5m"].metrics` EMA20 and latest completed 5m
   close for the trend filter. Require the snapshot's data gates to pass.
5. For derivatives confirmation, inspect `options.summary.pcr_oi`
   and its `pcr_scope`. PCR is **not** a directional prediction. With
   thresholds bullish >=0.95 and bearish <=1.05, PCR in [0.95,1.05]
   satisfies both; it cannot distinguish direction by itself.
6. Before emitting a Signal Card, check `entry_context_ready`,
   `account.pending_orders_count`, `account.open_positions_count`,
   and `account.broker_open_positions_count`. Never assume a local
   count of zero proves that the broker account is flat.
7. Include `snapshot_id`, the exact candle closing timestamps, quote
   source and timestamp, PCR scope, option quote coverage, proposed expiry,
   bid/ask spread, and explicit reasons for each signal condition.

The `entry_permitted` field means that the *observation context* is eligible
for further consideration. It does **not** grant permission to route a LIVE
order. The existing live preflight, risk engine, broker reconciliation and
order authorization remain definitive.

## Execution and exits (external scheduler / execution-engine work)

The one-minute language-model scheduler must not be the only mechanism
watching a protective stop, exit deadline, or broker order status.

- On confirmed fill, an independent deterministic worker/broker order plan
  must enforce a protective stop, take-profit, and any configured
  time/technical exit, with recovery after restarts.
- Model expected slippage, bid/ask spreads, delayed fills, partial fills and
  reject/unknown orders; do not assume a stop guarantees its trigger price.
- Reconcile order intent -> exchange acknowledgment -> actual fills and
  broker positions. Block duplicate signals and new entries while any
  submission is unresolved.
- Test trailing-stop alternatives using actual option bid/ask/quote history
  and event-time backtests before enabling them in LIVE mode.
- The AI-facing endpoints intentionally expose no `/schedule` or
  trade-placement endpoint. Those behaviors are external and require
  independent integration tests.

## Test and monitoring requirements

Cover invalid JWT, stale/future exchange times, simulated feed, incomplete
or missing 1m/5m bars, missing ATM legs, option-chain timeout, stale option
timestamps, conflicting broker/local positions, SUBMISSION_UNKNOWN orders,
overlapping scheduler cycles, and service restarts. Alert on persistent
`entry_permitted=false` and never convert failure into permissive fallback.

The regular-session weekday indicator is **not** a complete NSE holiday
calendar. Exchange-closed sessions must stay blocked through market-data
freshness checks, and a dedicated exchange-calendar provider should be added
before relying on session scheduling alone.
