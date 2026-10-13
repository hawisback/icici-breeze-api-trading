# AI-directed PAPER and LIVE execution

The external AI owns entry, stop-loss, modification, cancellation and exit decisions. The platform routes orders to PaperBrokerAdapter in PAPER and to Kite Connect in LIVE, while the UI remains read-only. Breeze may remain a reference-data source.

The mode is PAPER on every backend restart; the AI requests a mode switch through the authenticated mode API. Switching from LIVE back to PAPER is rejected if unresolved LIVE orders or positions exist. Following a restart, the service begins in PAPER; the AI can explicitly re-authorize LIVE to manage an existing journal-owned LIVE position/stop once the broker session is connected and PAPER is flat.

## Configuration

Set AI_ONLY_MODE=true, LIVE_EXECUTION_BROKER=kite and AI_EXECUTION_ENABLED=true. Supply a distinct secret, AI_EXECUTION_API_KEY, of at least 32 bytes. The AI sends the secret in X-AI-Execution-Key on every execution request. Market data may be hybrid, with FREQUENT_DATA_BROKER=kite and REFERENCE_DATA_BROKER=breeze.

Keep LIVE_TRADING_ENABLED=false and AI_EXECUTION_LIVE_SWITCH_ENABLED=false during PAPER testing. For supervised LIVE testing, explicitly enable both options, allowlist ZERODHA_PRIMARY, configure a strong AUTH_SIGNING_KEY, and connect the daily Kite broker session. Verify the public outgoing static IP is whitelisted at Kite.

## API endpoints

All routes begin with /api/v1/ai. GET /execution/status returns the mode and permissions. POST /execution/mode accepts mode PAPER or LIVE. POST /execution/orders accepts request_id, instrument_id, quantity and limit_price to place a BUY LIMIT entry. GET /execution/orders and GET /execution/orders/{order_id} reconcile and report broker status. DELETE /execution/orders/{order_id} cancels a pending or partially filled entry or exit, with broker verification. GET /execution/positions reports positions for the active mode. POST /execution/stops accepts request_id, entry_order_id, trigger_price and limit_price for a SELL SL-LIMIT. PATCH /execution/stops/{stop_order_id} accepts trigger_price and limit_price for a broker stop modification. DELETE /execution/stops/{stop_order_id} cancels the stop, subject to broker confirmation. POST /execution/exit accepts request_id, entry_order_id and limit_price to sell the verified remaining long position. GET /execution/monitor is a local read-only dashboard feed.

The entry_order_id for stops/exits is the API's local order_id, not Kite's broker order ID. For an exit, first cancel any pending stop and verify cancellation in Kite; otherwise the API rejects a competing SELL.

## Limitations and supervised testing

PAPER LIMIT orders are immediately filled by the existing mock broker at the requested price, and PAPER stop orders remain pending; they do not automatically trigger. The AI must explicitly request the exit. PAPER positions are held in memory and reset after a backend restart. LIVE SL-LIMIT orders can remain unfilled after trigger; broker acceptance is not fill confirmation. Do not retry UNKNOWN submissions blindly; use Kite orders/positions to reconcile. No automatic trading decisions or trailing-stop scheduler runs.

Before LIVE testing, verify API authentication, fresh Kite quotes, contract symbols, lot sizes and funds. Test PAPER entry, stop, modify, cancel and exit in that order. Test rejected/unknown order and mode-switch cases. Then, under direct supervision, validate one minimum-lot real trade and its broker-held stop. Stop new entries if order status or broker position cannot be verified. No broker orders are placed by this implementation or its tests.

## NIFTY option-premium technical indicators

GET /api/v1/ai/nifty/options/technicals returns each *listed NIFTY CE/PE option's own* completed Kite premium candles and objective indicators. This is NOT the NIFTY underlying/futures technical series and does not generate trading recommendations. The endpoint is read-only, limited to local callers and does not need the AI execution key.

Pass 1-6 repeated instrument_ids query parameters (not comma-separated), plus interval=1m, 5m or 15m and limit=50..500 (default 200):

~~~http
GET /api/v1/ai/nifty/options/technicals?instrument_ids=INST-NIFTY-2026-10-13-25000-CE&instrument_ids=INST-NIFTY-2026-10-13-25000-PE&interval=5m&limit=200
~~~

The example IDs illustrate the instrument identifier format, not guaranteed active contracts. Use the instrument_id values obtained from GET /nifty/options. Response contains source KITE, basis OPTION_PREMIUM, interval, requested_count, available_count, calculated_at and an ordered contracts array. Each element includes instrument_id, symbol, expiry, strike, right, lot_size, candle_count, data_through, age_seconds, missing_recent_session_bars, available and the same metrics schema as GET /nifty/technicals (including EMA, RSI, MACD, ATR, VWAP and session VWAP). The option's session VWAP is computed from its own trading volume; there is no NIFTY_FUTURES VWAP substitution.

Each option is matched to the exact Kite NFO instrument token by expiry, strike and CE/PE. Only completed, valid KITE candles are used. Fewer than 50 bars, a missing contract, disconnected broker, or a failed historical request returns available=false with an explicit reason for that contract rather than guessed data. Results are cached for 20 seconds. The backend limits batch size and spaces historical requests to reduce rate-limit bursts. Option history is limited by what Kite can return for the listed contract, particularly near expiry.
