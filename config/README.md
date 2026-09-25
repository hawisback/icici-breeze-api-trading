# Trading configuration

`config/trading.json` is the canonical **non-secret operator configuration** for
the strategy service.

It contains the complete `AutoTradingConfig`. Standard JSON does not support
comments, so the optional top-level `_meta` object contains human-readable
annotations. The loader ignores `_meta`, and repository writes preserve those
annotations across UI/API saves.

- global strategy execution mode: `PAPER`, `SHADOW_ONLY`, or `LIVE`
- master auto-trade and kill-switch controls
- option-selection rules
- risk limits and paper-cost assumptions
- session times
- Strategy A-E enable flags and tunables

The file path is selected by:

```text
TRADING_CONFIG_PATH=./config/trading.json
```

## Precedence and persistence

When `TRADING_CONFIG_PATH` exists, the strategy service loads that JSON at
startup and mirrors the validated configuration into
`data/strategy/strategy.db`.

When an operator saves strategy configuration through the UI/API, the validated
configuration is written to both the JSON file and SQLite. The JSON file is
therefore the visible durable source for normal operator settings.

The checked-in `config/trading.json` is now authoritative whenever it is
present. The repository still supports the legacy one-time
`bootstrap_from_database_if_present` marker for explicit migrations, but the
current checked-in operator config does not carry that marker. This prevents
stale SQLite values from silently overriding the file-selected trading mode or
strategy enable switches.

An invalid JSON file is a startup error; the service does not silently ignore a
malformed operator configuration.

## LIVE safety boundary

`system_armed` is shown in the JSON schema for completeness but is always
written as `false` and is forced to `false` when the file is loaded. Arming is
runtime-only and must be performed explicitly after startup.

The JSON file also does **not** replace the server-side LIVE capability gate.
These remain in `.env`:

- `LIVE_TRADING_ENABLED`
- `LIVE_EXECUTION_BROKER`
- `LIVE_ALLOWED_ACCOUNTS`
- broker API credentials/tokens
- market-data broker routing
- authentication/security settings

Therefore setting `"mode": "LIVE"` in `trading.json` does not by itself allow
live orders. The platform must also be LIVE-capable through environment
configuration, pass preflight/reconciliation checks, and be explicitly armed.

Conversely, `LIVE_TRADING_ENABLED=true` in `.env` does **not** force strategies
to trade LIVE. If the operator selects **PAPER** in the strategy UI, the UI
persists `"mode": "PAPER"`, forces `system_armed=false`, and all A-E execution
policies resolve to PAPER. Broker order routing is therefore not used for those
strategy entries. Switching back to LIVE also disarms and requires a fresh
runtime ARM after LIVE preflight passes.

## Recommended LIVE broker/data routing

The tracked `.env.local-live.example` is the recommended local LIVE profile.
Copy it to the gitignored `.env` on the trading host and replace all credential
placeholders.

Its routing is:

```text
LIVE orders / reconciliation      -> Zerodha Kite
Frequent quotes / candles/history -> Zerodha Kite
Reference / option chain / Greeks -> ICICI Breeze
```

The corresponding environment settings are:

```text
LIVE_TRADING_ENABLED=true
LIVE_EXECUTION_BROKER=kite
MARKET_DATA_BACKEND=hybrid
FREQUENT_DATA_BROKER=kite
REFERENCE_DATA_BROKER=breeze
```

Both broker sessions may be active at the same time. The execution broker owns
the entire real-order lifecycle; the data-routing roles do not split ownership
of a single order across brokers.

## Strategy enable switches

The primary strategy switches are under `tunables`:

```json
{
  "trend_pullback_enabled": true,
  "volatility_breakout_enabled": true,
  "di_continuation_enabled": true,
  "sr_momentum_breakout_enabled": true,
  "pivot_vwap_scalp_enabled": true,
  "strategy_e_countertrend_enabled": true
}
```

The UI's strategy activation controls edit these same fields; there is no
separate hidden enable store.
