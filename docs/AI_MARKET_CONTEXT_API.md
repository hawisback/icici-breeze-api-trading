# AI Market Context API

This API exposes read-only evidence plus an independent AI PAPER trade-intent endpoint for a scheduler-driven AI trading assistant.
The AI wakes on its own schedule (minimum one minute) and decides which endpoints
to call. Backend services prepare and normalize data but do not make the market or
trade decision.

## Principle

The backend may calculate objective measurements such as RSI, MACD, EMA, ATR,
VWAP, returns, option OI totals, OI changes, volume, PCR, timestamps and freshness.
It must not return directional recommendations, setup scores, confidence scores,
BUY/SELL labels, or a pre-ranked best option contract.

Synthetic market data is not accepted by these endpoints. If real Breeze/Kite data
is unavailable, responses mark the relevant section unavailable instead of silently
substituting simulated evidence.

## Endpoints

- GET /api/v1/ai/nifty/snapshot — compact starting packet for each AI cycle.
- GET /api/v1/ai/nifty/candles — 1m, 5m or 15m completed real-market candles.
- GET /api/v1/ai/nifty/technicals — objective RSI/MACD/EMA/ATR/VWAP measurements.
- GET /api/v1/ai/nifty/options — ATM-centered Kite option chain, session OI change and numeric aggregates.
- GET /api/v1/ai/heavyweights — one batched Kite NSE quote for HDFCBANK, RELIANCE, ICICIBANK, INFY and TCS.
- GET /api/v1/ai/account/context — portfolio, live broker account and safety state.
- GET /api/v1/ai/data-quality — feed, broker-session and quote freshness metadata.
- POST /api/v1/ai/trades — independent, mode-neutral AI PAPER trade intent.
- GET /api/v1/ai/trades — AI trade journal; GET /api/v1/ai/trades/{trade_id} — current trade state.

## AI workflow

A normal one-minute cycle starts with /nifty/snapshot. The AI can then drill into
/candles, /technicals, /options and /account/context as needed. All responses carry
source/freshness fields where the underlying service exposes them, allowing the AI
to reject stale or unavailable evidence.

The external AI alone decides whether a signal is actionable. Read-only
snapshot `entry_permitted` (schema 1.2) is a backwards-compatible alias
for `entry_data_ready`: **market-data diagnostics only**. Shared strategy
positions, global system risk mode, and OMS order state remain available
as account information but do not block `POST /api/v1/ai/trades` PAPER
submissions. The independent AI PAPER manager enforces only AI-specific
quantity/notional/open-position/daily limits and real Kite quote integrity.
LIVE routing by this API is not supported and fails closed. See
`docs/AI_SCHEDULER_INTEGRATION.md` for the mode-neutral trade lifecycle.


## Local POC setup and Kite option-chain diagnostics

The API base URL is `http://127.0.0.1:8000/api/v1/ai`. **No login, Bearer token,
or Authorization header is needed for any local AI endpoint (including POST
/trades).** Nonlocal callers are rejected with HTTP 403. Docker Compose
backend port is host-loopback-only (`127.0.0.1:8000:8000`). When running
`python run_platform.py` directly, set `API_HOST=127.0.0.1` in your environment
to achieve the same local-only boundary.

All normal option-chain consumers—including `/api/v1/options/chain`,
internal strategies, `GET /nifty/options` and snapshot option summaries—now
use **Kite NFO quotes and listed contracts** by default, independently of
whether Breeze remains the broker for LIVE order routing. A missing Kite
session returns `source=UNAVAILABLE`; there is no Breeze or synthetic
fallback for normal option-chain requests. Explicit `provider="breeze"`
remains a legacy diagnostic service option, not an AI trading route. Expiries and option tradingsymbols come from Kite
NFO instruments, **not** `instruments.db`. Missing/failed Kite responses never
silently substitute simulated quotes. Start the server with a valid Kite daily
session/access token and verify its broker session status.

Optional query parameters:
- `expiry=YYYY-MM-DD`: the requested **listed Kite expiry**, not an arbitrary
  date. Invalid dates are rejected by request validation (HTTP 422); an unlisted
  expiry returns `available=false`, `reason=KITE_EXPIRY_NOT_AVAILABLE`,
  and `available_expiries`.
- `strike_window=0..30`: number of strike levels either side of ATM to return
  (default 10). The adapter queries at most 61 strike levels / 122 contracts
  for the AI endpoint, rather than quoting all NFO instruments.

The API distinguishes `captured_at`/`age_seconds` (server retrieval time)
from `market_timestamp`/`market_data_age_seconds` (conservative earliest
available Kite quote timestamp). The latter are `null` if quote timestamps are
not provided for all returned contracts: retrieval time **does not** establish
market freshness. `partial_quote_coverage` indicates when some requested
contracts had no usable quotes. Missing individual contracts are omitted, not
zero-filled. Kite quote data does not provide an authoritative OI change, so
the AI API persists the first **fresh, exchange-timestamped** OI observation for each contract per IST trading session in `data/kite_session_oi.db`. `oi_change` is the current OI minus that first observed OI, **not** a previous-close comparison. On the first observation, missing timestamps, stale quotes, an out-of-session observation or a database error, the value remains `null`, not zero. Each leg carries `oi_change_basis=FIRST_OBSERVED_SESSION` and `oi_baseline_at`; the response advertises its basis. Comparison baselines survive direct-Uvicorn restarts. `pcr_oi` is also `null` when Kite omits an OI observation, rather than silently treating missing OI as zero.

The technicals endpoint retains historical `vwap` computed from the requested candles, but **never** interprets the NIFTY 50 index itself as a traded-volume series. For NIFTY index technicals, `metrics.session_vwap` now uses the nearest listed **Kite NIFTY future**, explicitly labelled by `metrics.session_vwap_basis=NIFTY_FUTURES`, `session_vwap_instrument_id`, and `session_vwap_data_through`. It fetches complete 5-minute futures bars starting at 09:15 IST, enforces exact contiguous coverage and positive actual volume, and caches the result for 55 seconds shared across 1m/5m/15m AI requests. If the source is unavailable, early-session, stale or has a missing bar, the value is `null` with `session_vwap_reason`. Futures VWAP is **not** the NIFTY spot index level; the basis distinction is economically important. The `regular_session` flag checks weekdays and clock time,
but does not yet implement the full NSE holiday/special-session calendar.

None of the read-only market-data response fields is a recommendation,
entry classification or authorization for LIVE trading. The AI decides
when to send a mode-neutral request; broker-protected LIVE trading will
need separate execution risk validation before it is enabled.

## Kite call-efficiency strategy

- A single bounded Kite option-chain quote batch (at most 122 contracts)
  is cached for 10 seconds and shared across the external AI, UI, internal
  strategies and AI PAPER trade monitor. Concurrent cache misses are
  deduplicated via an asynchronous lock. Cached quote exchange timestamps
  are never rewritten to look fresher than their real exchange times.
- Kite NFO expiry metadata is cached for 60 seconds, so every AI/worker
  read does not traverse the NFO master and refresh expiry lists again.
- When the existing market-feed service has an exchange-timestamped **Kite**
  index quote no older than 6 seconds, the option-chain adapter reuses it
  for ATM discovery rather than making an additional Kite index-LTP call.
  Missing, stale or Breeze/synthetic ticks trigger the real Kite-LTP fetch.
- The current AI-only dashboard calls only the AI snapshot and AI trade journal. Prefer one /nifty/snapshot per AI decision cycle; drill down into /nifty/options, /nifty/technicals and /heavyweights only when needed. Avoid repeatedly calling all APIs during a scan. Heavyweights quote retrieval sends **one** batch of five NSE instruments and caches the complete result for 15 seconds.
- Repeated API responses may contain the same quotes within a cache window;
  use **market_timestamp** / exchange observation age, never just captured_at,
  when considering a simulated trade. The PAPER manager continues to reject
  stale, missing, invalid or too-wide quotes.

## Direct Uvicorn smoke checks (local workstation)

Run `python run_platform.py` or `uvicorn services.api_gateway.main:app --host 127.0.0.1 --port 8000` after configuring the daily Kite session. No Docker or AI login is required.

```bash
curl http://127.0.0.1:8000/api/v1/ai/nifty/technicals?interval=5m\&limit=200
curl 'http://127.0.0.1:8000/api/v1/ai/nifty/options?strike_window=10'
curl http://127.0.0.1:8000/api/v1/ai/heavyweights
curl 'http://127.0.0.1:8000/api/v1/ai/nifty/candles?interval=15m\&limit=20'
```

Session OI deltas need two distinct, fresh exchange observations of the same strike/expiry; the first establishes the session baseline. They do not become previous-close OI changes. The OI baseline SQLite file is **not** the legacy OMS PAPER order book and must not be reset alongside it. These features provide raw measurements; the external AI alone classifies traps or trade entries. Kite session activation and broker-real data must be verified on the local running instance.
