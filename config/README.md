# Trading configuration

`config/trading.json` is the canonical **non-secret operator configuration** for
the strategy service.

It contains the complete `AutoTradingConfig`:

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

For rollout compatibility, the checked-in seed file carries a one-time
`bootstrap_from_database_if_present` marker. If an existing SQLite
configuration is present, that existing operator state wins on the first
startup and is exported into the JSON file; the marker is then consumed. Fresh
installations use the checked-in JSON defaults. If neither file nor database
state exists, model defaults are created and saved to both.

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

## Strategy enable switches

The primary strategy switches are under `tunables`:

```json
{
  "trend_pullback_enabled": true,
  "volatility_breakout_enabled": true,
  "di_continuation_enabled": true,
  "sr_momentum_breakout_enabled": true,
  "pivot_vwap_scalp_enabled": false,
  "strategy_e_countertrend_enabled": true
}
```

The UI's strategy activation controls edit these same fields; there is no
separate hidden enable store.
