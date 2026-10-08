# AI scheduler integration contract

This document describes the market-data contract and the independent mode-neutral
PAPER trade-intent API for an external NIFTY AI scheduler. AI—not the backend
strategy engine—classifies trade opportunities and chooses when to request entry.
The backend validates the specific simulated contract and manages exits.
It never converts an AI signal into a LIVE broker order on this API.

## Local access (no login or JWT)

All `/api/v1/ai/*` routes, including `POST /trades`, accept localhost
HTTP requests **without any Authorization header**. Use
`http://127.0.0.1:8000/api/v1/ai` directly. Do not call `/auth/login`.

This is **not** an open network API: nonlocal TCP clients are rejected
with HTTP 403 (`AI_API_LOCAL_ONLY`), even if they send spoofed Host or
X-Forwarded-For headers. Run `python run_platform.py` with
`API_HOST=127.0.0.1`; never expose unauthenticated AI trade routes on
a public interface.

Docker Compose already publishes only `127.0.0.1:8000:8000` on the
host. Because Docker may relay that connection from its bridge gateway,
Compose explicitly sets `AI_TRUST_LOCAL_DOCKER_GATEWAY=true` for this
single localhost-bound deployment. Do not enable that option with
publicly reachable host port mappings or untrusted container networking.
The rest of the platform retains its existing authentication and RBAC.
The external scheduler must run on the same workstation or use a secure,
locally terminated tunnel into the workstation's loopback interface.

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
Content-Type: application/json

{"signal_id":"signal-20261008-1033-pe","instrument_id":"INST-NIFTY-2026-10-13-22500-PE","quantity":65}
```

`signal_id` is a durable idempotency key. The AI chooses a live
Kite-listed NIFTY option instrument from `GET /nifty/options`, but **never**
the fill price, stop, execution mode or trailing policy. The server
requires a real exchange-timestamped Kite contract quote, an acceptable
spread, lot-size alignment and **AI-specific** quantity/notional/daily/open
trade caps. Internal strategy positions, the strategy kill switch, the
global risk mode, broker portfolio and OMS orders **do not veto AI PAPER**
submissions. They may still appear in account context as evidence. In PAPER, the assumed entry is the *observed ask*; exits use
the *observed bid*. These are simulated fills, not guaranteed executable
market prices. The response omits the backend's execution mode.

Use `GET /api/v1/ai/trades` and `GET /api/v1/ai/trades/{trade_id}` to
track state. The same generic state schema is returned for a repeated
`signal_id`. No JWT, session login, or trader role is required for
these local AI endpoints. Nonlocal callers are denied.

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

This PAPER lifecycle is intentionally separate from the existing strategy
engine, OMS and risk/kill-switch state. Neither engine blocks the other's
PAPER trade choices: the AI manager limits its own open trade, and the
strategy engine manages its own ledger. Both use shared market-data services,
but their trading state and entry classifiers are independent. Simulated AI
positions are not consolidated into the existing strategy/OMS portfolio totals.
A future LIVE implementation must reintroduce broker-wide protection,
reconciliation and global emergency controls before any LIVE routing.

## Scheduler cycle

1. Once per minute during the configured Indian market trading window,
   fetch **one** `GET /api/v1/ai/nifty/snapshot` and retain its `snapshot_id`.
   The snapshot deliberately uses the same ten-strike window as the default
   `/nifty/options` PCR scope.
2. Interpret `entry_data_ready`, `entry_permitted` (legacy data-only alias)
   and `blocking_reasons` strictly as **market-data diagnostics**, not a
   signal classifier or server trading authorization. Use the raw source/
   timestamp evidence to decide if data meets your own strategy needs.
   Never treat missing, unavailable or fabricated prices as valid fills.
3. Use `technicals["1m"].metrics` for the latest *completed-bar*
   `macd_crossed_above_zero`, `macd_crossed_below_zero`,
   `macd_histogram_expanding_positive`,
   `macd_histogram_expanding_negative`, `rsi_crossed_above_55`,
   and `rsi_crossed_below_45` fields. Inspect `bar_end_time`,
   `macd_histogram_prev`, `rsi_14_prev` and `data_through`.
   Never infer a crossover merely from a current RSI/MACD threshold.
4. Use `technicals["5m"].metrics` EMA20 and latest completed 5m
   close for the trend filter. The external AI owns all trend/entry rules.
5. For derivatives confirmation, inspect `options.summary.pcr_oi`
   and its `pcr_scope`. PCR is **not** a directional prediction. With
   thresholds bullish >=0.95 and bearish <=1.05, PCR in [0.95,1.05]
   satisfies both; it cannot distinguish direction by itself.
6. Account/OMS/risk details remain visible as **informational diagnostics**,
   including strategy positions and global risk mode. Do not use their
   `entry_context_ready`, `entry_blockers`, `pending_orders_count` or
   strategy kill-switch state to veto a separate AI PAPER decision. Only the
   AI journal's own open trades govern AI trade overlap.
7. Include `snapshot_id`, the exact candle closing timestamps, quote
   source and timestamp, PCR scope, option quote coverage, proposed expiry,
   bid/ask spread, and explicit reasons for each signal condition.

Snapshot schema 1.2 retains `entry_permitted` for compatibility, but it
now equals `entry_data_ready` and `blocking_reasons` contains **data-only**
diagnostics. `readiness_scope=MARKET_DATA_ONLY`. These fields never judge
bullish/bearish classification and are not checked by `POST /trades`.
The separate account `entry_context_ready` is a diagnostic of the existing
platform's global risk/OMS state, **not an AI PAPER order gate**.
The LIVE broker execution route remains fail-closed.

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

Cover nonlocal peer rejection, forged forwarded headers, stale/future
exchange times, simulated feed, missing
ATM legs, option-chain timeout, stale option timestamps, AI-specific duplicate
signals/open trades, conflicting quantities, service restarts, and isolated
strategy/global-risk/OMS states. Report missing or stale input data honestly;
never substitute fabricated quote prices for valid PAPER fills.

The regular-session weekday indicator is **not** a complete NSE holiday
calendar. Exchange-closed sessions must stay blocked through market-data
freshness checks, and a dedicated exchange-calendar provider should be added
before relying on session scheduling alone.
