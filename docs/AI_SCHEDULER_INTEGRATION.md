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

## Mode-neutral AI trade submission (PAPER first)

The external AI scheduler **does not** know or choose the execution mode.
The backend owns its own separate config in `.env`; restart the backend
after operator changes. Defaults are **enabled for PAPER**, never LIVE.

```dotenv
AI_TRADE_ENABLED=true
AI_TRADE_MODE=PAPER
AI_TRADE_MAX_QUANTITY=65
AI_TRADE_MAX_PREMIUM_NOTIONAL=15000
AI_TRADE_MAX_DAILY_ENTRIES=3
AI_TRADE_INITIAL_STOP_PCT=6
AI_TRADE_TRAIL_ACTIVATION_PCT=5
AI_TRADE_TRAIL_GAP_PCT=3
AI_TRADE_TARGET_PCT=7
AI_TRADE_MAX_HOLD_SECONDS=480
AI_TRADE_POLL_SECONDS=2.5
```

To disable new AI paper entries, set `AI_TRADE_ENABLED=false` and restart
 the backend. Existing open simulated trades remain monitored until closed.
Sending `PAPER` or `LIVE` in a trade request is
rejected as an unknown field. **If the operator selects LIVE, this new
adapter blocks submissions** with `EXECUTION_MODE_NOT_READY`. The existing
OMS/Risk/LiveTradingGate/protective-stop services must be integrated and
end-to-end validated for externally originated trades *before* allowing
LIVE; there is no silent mode fallback.

On a validated, fresh signal from the external scheduler:

```http
POST /api/v1/ai/trades
Authorization: Bearer <trader-scoped-token>
Content-Type: application/json

{"signal_id":"signal-20261008-1033-pe","instrument_id":"INST-NIFTY-2026-10-13-22500-PE","quantity":65}
```

`signal_id` is a durable idempotency key. The AI chooses a live
Kite-listed NIFTY option instrument from `GET /nifty/options`, but **never**
the fill price, stop, execution mode or trailing policy. The server
requires context readiness, flat reconciled account, no open strategy
trade, no unresolved orders, a real exchange-timestamped contract quote,
a valid spread, lot-size alignment and operator quantity/notional/daily
caps. In PAPER, the assumed entry is the *observed ask*; exits use
the *observed bid*. These are simulated fills, not guaranteed executable
market prices. The response omits the backend's execution mode.

Use `GET /api/v1/ai/trades` and `GET /api/v1/ai/trades/{trade_id}` to
track state. The same generic state schema is returned for a repeated
`signal_id`. Trader/Admin authorization is required; the generic
market-context endpoints remain read-only.

An independent backend loop polls Kite contract quotes every 2.5 seconds
(configurable). It persists entry, trailing state, last observed quote,
and exit/reason in `AI_TRADE_DB_PATH` (default `./data/ai_trades.db`).
The loop resumes persisted open PAPER trades after backend restarts:

1. Fixed stop initially **6% below simulated ask fill**.
2. On observed bid reaching **+5% from entry**, activate a stop no lower
   than breakeven, then ratchet at **3% below peak observed bid**.
3. Exit on stop breach, **+7% target**, or **8-minute maximum hold**,
   using the next available valid observed bid. Stops only ratchet upward.
4. Stale/unavailable option quotes do **not** produce fictional exits;
   `data_status` records the outage. A PAPER trade can remain open
   longer than planned during an outage. The operator must monitor these
   states; no broker protective order exists for PAPER simulated fills.
5. `pnl` is **gross simulated premium P&L** before fees and slippage.

This PAPER lifecycle is intentionally separate from the existing
strategy-generated trade ledger. It checks strategy active positions and
kill switch to avoid overlapping position authority. A future release
should unify PAPER ledger reporting and the OMS/PAPER broker adapter,
and separately wire broker-held protective orders for LIVE.

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
- `/schedule` remains external; the backend offers one restricted
  mode-neutral trade-intent endpoint, not direct order placement authority.
  The new trade endpoint is PAPER-only until LIVE safety integration passes.

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
