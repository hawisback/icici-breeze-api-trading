# AI Market Context API

This API is a read-only evidence layer for a scheduler-driven AI trading assistant.
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
- GET /api/v1/ai/nifty/options — ATM-centered option chain plus numeric aggregates.
- GET /api/v1/ai/account/context — portfolio, live broker account and safety state.
- GET /api/v1/ai/data-quality — feed, broker-session and quote freshness metadata.

## AI workflow

A normal one-minute cycle starts with /nifty/snapshot. The AI can then drill into
/candles, /technicals, /options and /account/context as needed. All responses carry
source/freshness fields where the underlying service exposes them, allowing the AI
to reject stale or unavailable evidence.

Order execution is intentionally outside this API. Any future AI-proposed order
must continue through the platform's deterministic risk, OMS and execution safety
boundaries.
