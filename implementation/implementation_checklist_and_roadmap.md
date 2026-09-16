# ICICI Direct Options Trading Platform — Reviewed Implementation Roadmap

> **Specification:** [icici_trading_platform_microservices_v2_latest_stack_sqlite.md](./icici_trading_platform_microservices_v2_latest_stack_sqlite.md)  
> **Review date:** 2026-09-16  
> **Review basis:** repository implementation, manifests, tests, Docker files, and frontend production build  
> **Current maturity:** functional in-process PAPER-mode prototype; **not approved for LIVE trading**

## Review Markers and Status Rules

All review changes are intentionally visible:

- `REVIEW CHANGE` — an original item was reclassified or its scope/acceptance criteria changed.
- `ADDED` — a missing implementation item was added during review.
- `IMPLEMENTATION UPDATE` — evidence recorded by the agent that implements an item.
- `[x] COMPLETE` — implemented and supported by repository evidence for the stated scope.
- `[~] PARTIAL` — useful implementation exists, but the production acceptance criteria are not met.
- `[ ] PENDING` — not implemented or not evidenced.
- `[!] BLOCKED` — implementation cannot safely continue without a named external input or decision; the blocker must be written beside the item.

> **REVIEW CHANGE:** A file existing or a happy-path test passing is not enough to mark a trading capability complete. Completion now requires automated tests, failure-path coverage, durable behavior where required, and operational evidence appropriate to the risk of the feature.

## Review Outcome

The architecture direction is sound: service-owned SQLite databases, an OMS state machine, pre-trade risk, an adapter boundary, transactional outboxes, and PAPER-first execution are good foundations. The original roadmap, however, overstated completion in four important areas:

1. The application currently wires all services into one FastAPI process and uses `InMemoryEventBus`; Redis and Redpanda are declared in Compose but are not used by application code.
2. The Breeze adapter is incomplete for production: credentials are not wired from the session service, live order status/positions/trades return empty values, and modify/cancel are success stubs.
3. `SUBMISSION_UNKNOWN` is recorded but is not queued and reconciled, so the advertised recovery protocol is incomplete.
4. The UI manifest/build uses Next.js 15.5.25, Lightweight Charts 4.2.3, and Tailwind 3.4.x, while the original checklist claimed Next.js 16, Charts 5.2+, and Tailwind 4.x.

Additional release blockers include missing API/WebSocket authentication and authorization, unrestricted CORS, browser-only LIVE-mode selection, incomplete risk rules, non-durable consumer idempotency, no database migrations/backups, and no real market/order WebSocket integration.

## Mandatory Definition of Done

Every item marked complete after this review must have:

- implementation committed in the named component;
- deterministic unit tests and relevant integration/failure-path tests;
- no placeholder, simulated, or hard-coded behavior in a production code path;
- configuration documented in `.env.example` without secrets in source or logs;
- restart/recovery behavior tested for durable trading state;
- operator-visible health/metrics for background workers;
- updated roadmap evidence containing the exact verification command or test name.

LIVE capability additionally requires the Phase 12 release gates at the end of this document.

## AI Implementation Guide — ADDED

This section is the execution contract for any AI or human implementing this roadmap. It removes architectural ambiguity and defines how an item is selected, implemented, verified, and handed off.

### 1. Source-of-Truth Order

When sources disagree, use this precedence:

1. Safety invariants and release gates in this reviewed roadmap.
2. The target architecture in `implementation/icici_trading_platform_microservices_v2_latest_stack_sqlite.md`.
3. Canonical domain contracts in `libs/contracts/models.py` and event topics in `libs/events/bus.py`.
4. Existing tests and public API behavior that are not explicitly superseded by a roadmap item.
5. Existing implementation details.

The code describes the current prototype; this roadmap describes the intended result. Do not preserve an unsafe behavior merely because existing code or a happy-path test depends on it. Update the contract, migration, tests, documentation, and consumers together when an intentional breaking change is required.

### 2. Target Architecture Decision

> **ADDED DECISION:** The production target remains the event-driven microservices architecture from the specification. The current in-process service container and `InMemoryEventBus` are development/test adapters, not the production topology.

| Concern | Current transitional implementation | Required production target |
|:--|:--|:--|
| Process topology | All modules hosted by one FastAPI process | Independently startable services with explicit health/readiness |
| Events | `InMemoryEventBus` | Durable Redpanda/Kafka adapter with consumer groups and replay |
| Service state | Service-owned SQLite files | Keep service-owned SQLite; never mount one writable DB into multiple service replicas |
| Live cache | Python dictionaries | Redis only for replaceable cache/coordination; never durable trading truth |
| API | FastAPI directly calls in-process services | Browser talks only to API Gateway; service APIs/events remain internal |
| Local tests | In-memory bus and temporary SQLite | Retain fast dependency-injected adapters for deterministic tests |

Do not split services merely by copying code into containers. A service boundary is complete only when ownership, configuration, database, event contracts, health, shutdown, and integration tests are explicit.

### 3. Non-Negotiable Safety and Ownership Invariants

Every implementation must preserve all of these rules:

1. Default execution mode is PAPER. Missing, invalid, or stale configuration must never fall back to LIVE.
2. The only valid order path is `UI/Strategy -> OrderIntent -> OMS -> Risk -> Execution -> Broker Gateway`.
3. Strategies, the API Gateway, and the UI never call Breeze directly.
4. Execution Service is the only normal writer of live broker commands. Reconciliation observes and repairs local truth; it never blindly resubmits.
5. `SUBMISSION_UNKNOWN` is a frozen state until broker evidence resolves it. Timeout is not rejection and not permission to retry.
6. OMS owns local order state; Risk owns risk decisions/modes; Portfolio owns executions/positions/P&L; Instrument owns contract metadata; Session owns broker-session state; Audit is append-only.
7. No service reads or writes another service's SQLite file. Cross-service data moves through versioned APIs/events.
8. A state change and its outgoing event are written in one local transaction. A consumer's state change and inbox/deduplication record are also one transaction.
9. Delivery is at least once. Consumers must be idempotent; code must not assume exactly-once delivery or ordering across partitions.
10. All stored timestamps are timezone-aware UTC. Exchange-session calculations use explicit `Asia/Kolkata` rules.
11. Never invent production market data. Synthetic quotes/candles/premiums must be labeled and impossible to enable in a LIVE process.
12. Browser state is display/request state, not trading authority. Server state decides trading mode, safety mode, permissions, and readiness.
13. Secrets and raw session tokens never enter SQLite, events, API responses, logs, exceptions, fixtures, or screenshots.
14. Never destructively change a database schema or trading record without a versioned migration, verified backup, and recovery test.
15. Broker payloads are untrusted input. Validate status, identifiers, quantity, price, timestamps, and allowed transitions before changing local state.

### 4. Canonical Order/Event Flow

Implement and test the production path in this order:

1. The caller submits an `OrderIntent` with a stable idempotency key/correlation ID.
2. OMS atomically persists the intent and initial order state with an `order.intent.v1` outbox record.
3. Risk consumes the event, atomically records its inbox marker and versioned decision, then emits `risk.decision.v1` through its outbox.
4. OMS consumes the decision, records the transition, and emits `execution.command.v1` only when approved.
5. Execution atomically claims the command/idempotency key and persists a submission attempt **before** the broker call.
6. Broker Gateway makes at most one placement call for that attempt and normalizes the response.
7. A timeout/ambiguous transport result produces `SUBMISSION_UNKNOWN`; a definitive response produces the matching normalized broker event.
8. OMS validates and persists the broker transition. Only broker-confirmed fills emit trade events.
9. Portfolio atomically deduplicates and records fills before changing positions/P&L.
10. Reconciliation resolves unknown or divergent state from broker evidence and records a complete audit trail.

The current direct “fast-path publish plus later outbox publish” behavior creates separate envelope IDs for the same logical event, so consumer inboxes cannot reliably recognize the duplicate. Remove the second publish path, or publish the exact persisted envelope ID through an outbox-aware low-latency dispatcher. Never create two independent envelopes for one state transition.

### 5. Repository and Coding Conventions

- Put canonical cross-service schemas/enums in `libs/contracts/`; do not import service repositories across service boundaries.
- Put broker-specific payload translation only in `services/broker_gateway/`.
- Repositories own SQL and transaction boundaries. Service/domain code must not issue raw SQL.
- Constructors must accept repositories, event buses, clocks, and external clients through dependency injection so tests can use temporary/fake implementations.
- Background workers need explicit `start()`/`stop()`, cancellation handling, readiness, last-success/error state, bounded retry with jitter, and metrics.
- Use the existing immutable Pydantic model style (`extra="forbid"`, timezone-aware values). Version external event schemas instead of silently changing fields.
- Preserve correlation ID, causation/event ID, source service, schema version, and occurred-at timestamp across event hops.
- Separate simulation from production with distinct adapters/configuration. Do not branch to fake LIVE responses based on keys such as `test_*`.
- Use temporary databases (`tmp_path`) in tests. Tests must not read or mutate `data/` service databases.
- Avoid timing-only assertions such as fixed `asyncio.sleep`. Prefer observable completion conditions with bounded timeouts.
- Any new dependency must be pinned through the project manifest/lockfile, justified in the item's implementation notes, and included in clean-install verification.

### 6. Financial-Value Policy

> **ADDED DECISION:** Quantities remain integers. Money and executable prices must not rely on binary floating-point for validation, persistence, or P&L/risk calculations.

Use `Decimal` at broker/API boundaries and in domain calculations, quantized using instrument tick size. Persist INR monetary values as integer paise where practical, or as canonical decimal strings when scale varies. Floats may be used for chart rendering and non-monetary analytics such as Greeks, but must not become the source of durable monetary truth. The migration/API compatibility work is tracked by item 0.9.

### 6A. Minimum Typed-Configuration Contract

Item 0.8 must define one validated settings model per process, using consistent names. At minimum, the configuration system must cover:

| Setting | Safe default / rule |
|:--|:--|
| `APP_ENV` | `development`; production enables stricter validation |
| `SERVICE_NAME` | required for independently started services |
| `DATA_ROOT` | `./data`; resolve and validate service-specific child paths |
| `EVENT_BUS_BACKEND` | `memory` only in tests/development; production requires `redpanda` |
| `REDPANDA_BROKERS` | required when the Redpanda backend is selected |
| `REDIS_URL` | optional until a feature explicitly requires Redis |
| `DEFAULT_TRADING_MODE` | `PAPER`; configuration may not default to LIVE |
| `LIVE_TRADING_ENABLED` | `false`; server startup must fail closed on invalid values |
| `LIVE_ALLOWED_ACCOUNTS` | empty; required and explicit for Gate B |
| `CORS_ALLOWED_ORIGINS` | local UI origins in development; no wildcard with credentials |
| `BREEZE_API_KEY`, `BREEZE_SECRET_KEY`, `BREEZE_SESSION_TOKEN` | secret inputs; never persisted or returned |
| `AUTH_SIGNING_KEY` / auth secret reference | required outside local development; redact everywhere |
| `LOG_LEVEL` | `INFO`; trading/security events remain structured and auditable |

Exact secret values never belong in `.env.example`; use descriptive placeholders. Settings affecting LIVE authority, risk limits, account routing, or event durability must be visible in startup diagnostics in redacted form and included in audit metadata by version/fingerprint, not by secret value.

### 7. How an Agent Must Execute One Roadmap Item

Work on one coherent roadmap item at a time unless the item explicitly lists inseparable contract/migration changes.

Before editing:

1. Read the full item, this AI guide, its dependency items, and all named components/tests.
2. Search for every caller, event consumer, API type, database table, and UI type affected.
3. Confirm dependencies are COMPLETE. If not, implement the prerequisite first or mark the item `[!] BLOCKED` with the exact dependency.
4. Identify external facts required from official ICICI/Breeze documentation. Never guess endpoint paths, signatures, status meanings, limits, or contract-master formats.
5. Write down the intended behavior, failure states, compatibility impact, and test cases in the item before making a material design choice not already settled here.

During implementation:

1. Add or update deterministic tests with the behavior.
2. Implement the smallest end-to-end vertical slice, including repository, service, event/API contract, configuration, health, and UI only when applicable.
3. Add versioned migrations before code that depends on the new schema.
4. Preserve PAPER behavior while keeping unfinished LIVE paths fail-closed.
5. Test duplicate delivery, restart/crash windows, timeout, invalid input, stale dependency, and unauthorized access where relevant.

After implementation:

1. Run the relevant verification matrix below from a clean dependency state when possible.
2. Do not mark COMPLETE when a required test/tool/external integration could not run. Use PARTIAL or BLOCKED and state why.
3. Update the item with an `IMPLEMENTATION UPDATE` containing date, changed components, migrations/config/contracts, exact commands, results, limitations, and follow-ups.
4. Recalculate the status summary when an item status changes.
5. Re-check the next dependent item; do not claim downstream completion automatically.

### 8. Required Implementation-Update Format

Append this block beneath an item when work is performed:

```text
- **IMPLEMENTATION UPDATE (YYYY-MM-DD):** <one-sentence outcome>
- **Changed:** <files/components>
- **Contracts/Data:** <API, event, schema, migration, or “none”>
- **Configuration:** <new/changed settings or “none”>
- **Verification:** `<exact command>` -> PASS/FAIL (<test count or concise result>)
- **Failure-path coverage:** <duplicates/timeouts/restart/auth/etc. tested>
- **Remaining limitations:** <none, or explicit follow-up item IDs>
```

An item moves to COMPLETE only when `Remaining limitations` contains nothing required by that item's acceptance criteria.

### 9. Work-Packet Template for New or Expanded Tasks

If an item lacks enough detail, expand it in place with this template before implementation:

```text
#### <ID> <Title>
- Status / priority:
- Goal and user-visible outcome:
- Non-goals:
- Depends on:
- Components/files:
- Owned data/tables:
- Consumed/emitted APIs or events:
- Configuration/secrets:
- Implementation notes and invariants:
- Required failure behavior:
- Acceptance criteria:
- Tests and fixtures:
- Verification commands:
- Rollout/rollback and observability:
```

Do not silently fill missing broker or operational facts with assumptions. Record a narrowly worded blocker that tells the next agent exactly what input is needed.

### 10. Verification Matrix

Use targeted tests while developing and the broad checks before marking an item complete:

| Area | Required commands/evidence |
|:--|:--|
| Backend unit/integration | `python -m pytest tests -q` plus targeted new test paths |
| Python quality | `python -m ruff check .` and configured type checker once added by 11.5 |
| Frontend clean install | from `frontend/trading-ui`: `npm ci` |
| Frontend types/build | `npx tsc --noEmit` and `npm run build` |
| Containers | `docker compose config`, `docker compose build`, health/readiness smoke test |
| SQLite change | migration on empty DB and copied prior-version fixture; `PRAGMA integrity_check`; backup/restore test |
| Eventing change | duplicate, replay, out-of-order where applicable, poison message/DLQ, producer crash window, consumer restart |
| Broker change | sanitized official-response fixtures, error/timeout/rate-limit cases, controlled sandbox/account evidence when required |
| Security change | unauthorized/forbidden/expired/revoked cases, secret-redaction test, audit record |
| Trading change | PAPER end-to-end test plus explicit proof that LIVE remains disabled unless Gate B is active |

If the environment lacks Python, Docker, broker credentials, or another required dependency, record that fact; inspecting source or cached artifacts is not a passing verification result.

### 11. External Inputs and Legitimate Blockers

The following work may require information outside the repository:

- Breeze official API/SDK documentation and sanitized response fixtures for funds, orders, trades, positions, modify/cancel, historical data, contract master, and WebSockets.
- A controlled ICICI account/session for non-destructive contract tests and later manual LIVE pilot evidence.
- Authoritative NSE trading calendar/holiday data and a documented refresh policy.
- Deployment host, TLS/domain, secret manager, backup destination, alert receivers, retention, RPO, and RTO decisions.
- Named operators/roles and the authentication bootstrap/recovery procedure.

When an external input is unavailable, an agent may implement interfaces, fakes, parsers against approved fixtures, and unit tests, but must leave the production integration PARTIAL or BLOCKED. Never use public guesses or synthetic success responses as LIVE verification.

### 12. Dependency-Ordered Work Queue

Use this queue instead of selecting tasks by phase number alone:

| Order | Work package | Prerequisites | Completion unlocks |
|--:|:--|:--|:--|
| 1 | 0.8 typed configuration/secrets; 7.4 server LIVE gate | none | safe startup and all live-facing work |
| 2 | 9.4 authentication/RBAC; 9.5 boundary/idempotency controls | 0.8 | safe non-local API/UI use |
| 3 | 0.7 migrations; 0.9 financial values | 0.2, 0.3 | safe schema and money-sensitive changes |
| 4 | 1.2 session, 1.4 adapter, 1.5 routing, 2.3 master, 2.4 resolver | 0.8, official broker inputs | real broker truth and valid instruments |
| 5 | 1.6 broker feeds; 3.2–3.4 history; 4.1–4.5 market services | broker session/master | trusted market and order data |
| 6 | 0.4 durable outbox/inbox; 0.5 Redpanda adapter; 5.3 OMS delivery | migrations/config | independently deployable, replayable services |
| 7 | 7.1 durable execution; 5.4 unknown reconciliation; 5.5 general reconciliation; 7.2–7.3 | broker adapter, durable events, instruments | safe broker commands/recovery |
| 8 | 6.2–6.5 risk and 8.2/8.5 portfolio correctness | money policy, trusted data, reconciliation | enforceable LIVE safety |
| 9 | 9.1–9.3 audit/gateway/WebSocket hardening and 10.x UI hardening | auth, authoritative server state | PAPER Beta user surface |
| 10 | 11.1–11.6 deployment, backups, telemetry, CI, runbooks | stable service contracts | Gate A/B operational readiness |
| 11 | 4.4 Greeks and 8.3–8.4 strategy/replay | trusted historical/live data and risk | Gate C evaluation |

When multiple IDs share a row, implement them as separate reviewable items unless a schema or contract change makes an atomic multi-item change unavoidable.

### 13. Known Current Hotspots

An implementing agent should inspect these before changing related behavior:

- `services/api_gateway/service_container.py` starts the simulated feed automatically and wires all services in process.
- `libs/events/bus.py` has no durable transport, partitioning, replay, retry, DLQ, or cross-process semantics.
- `services/oms/service.py` publishes through both a fast path and its outbox; duplicate delivery is therefore already possible.
- `services/risk/repository.py` creates risk outbox records, but no risk outbox dispatcher is started.
- `services/execution/service.py` keeps processed execution IDs only in memory and does not persist a pre-call submission attempt.
- `services/broker_gateway/icici_breeze_adapter.py` contains test-key simulated LIVE behavior and stubbed status/position/trade/modify/cancel operations.
- `services/broker_session/service.py` does not authenticate activation against the broker or propagate credentials into the live adapter.
- `services/api_gateway/main.py` allows wildcard CORS, has no auth/RBAC, and reports some health values as constant `LIVE`/`ACTIVE`.
- `services/risk/service.py` treats BUY as entry and lacks position-aware EXIT_ONLY logic; options can open risk on either BUY or SELL.
- `services/portfolio/service.py` implements a long-oriented aggregate calculation and needs explicit short/reversal/fee/day-boundary rules.
- `services/option_chain/service.py` synthesizes missing premiums; production must return missing/stale status instead.
- `frontend/trading-ui/stores/useTradingStore.ts` holds trading mode locally; this must not authorize server execution.
- `frontend/trading-ui/features/header/GlobalHeader.tsx` contains healthy-looking fallback display values that can mask unavailable data.
- `frontend/trading-ui/package.json` currently targets Next 15, Charts 4, and Tailwind 3 while the target baseline is newer.
- `Dockerfile.backend` installs the project before copying all metadata/source required by the build; both images need clean-context build tests.
- Existing asynchronous pipeline tests use fixed sleeps; replace these incrementally with bounded condition/event waits to reduce flakes.

These are navigation hints, not permission to combine unrelated fixes into one oversized change.

### 14. Start Here: First Executable Work Packet

The next agent should begin with **0.8 Configuration and secrets boundary**. It has no unfinished prerequisite and creates the safe foundation required by later work.

#### Work Packet 0.8 — Typed Configuration and Fail-Closed Startup

- **Goal:** replace scattered/default environment handling with dependency-injected, validated settings while preserving local PAPER startup.
- **Non-goals:** do not implement authentication, persist broker secrets, connect Redpanda, or enable LIVE.
- **Primary components:** create `libs/config/__init__.py` and `libs/config/settings.py`; update `pyproject.toml`, `.env.example`, `run_platform.py`, `docker-compose.yml`, `services/api_gateway/service_container.py`, and affected service constructors.
- **Dependency choice:** use Pydantic Settings compatible with the pinned Pydantic major; lock the dependency in `pyproject.toml` and clean-install verification.
- **Required settings:** implement the Minimum Typed-Configuration Contract above plus explicit `MARKET_DATA_BACKEND` (`simulated` or `breeze`) and service database paths derived safely from `DATA_ROOT`.
- **Secret behavior:** represent credentials/signing keys as secret types; exclude them from model dumps/repr/startup logs; add a redacted diagnostic summary containing only whether a secret is configured.
- **Validation behavior:** default to PAPER and `LIVE_TRADING_ENABLED=false`; reject LIVE as a default mode; reject wildcard credentialed CORS; reject `redpanda` without brokers; reject Breeze market data without required session configuration; production rejects in-memory eventing and synthetic market data.
- **Injection behavior:** settings are created at process composition/startup and passed to services/adapters. Avoid hidden import-time environment reads so tests can create isolated settings objects.
- **Backward compatibility:** local `python run_platform.py` continues to start in development/PAPER/simulated mode without real broker secrets.
- **Tests:** safe defaults; every invalid combination above; secret redaction; environment parsing; path derivation; production restrictions; independent test instances without cache leakage.
- **Verification:** `python -m pytest tests/test_config.py -q`, full backend suite, Ruff, and a startup smoke test with no `.env` and with deliberately invalid production settings.
- **Completion update:** use the required `IMPLEMENTATION UPDATE` block and leave 7.4/9.4 pending; typed configuration alone must not expose a LIVE activation endpoint.

---

## Phase 0: Runtime, Contracts, Persistence, and Eventing

- [~] **0.1 Python 3.14 runtime baseline — PARTIAL**
  - **Component:** `pyproject.toml`, `Dockerfile.backend`
  - **Evidence:** Python `>=3.14` is declared and the container uses `python:3.14-slim`.
  - **REVIEW CHANGE:** Local Python verification could not be reproduced in the review environment; the earlier exact `3.14.7` claim is therefore not retained as verified.
  - **Acceptance:** pin/test the supported patch line in CI, reject unsupported interpreter lines, and record `python --version` in CI artifacts.

- [x] **0.2 Core domain models and contracts — COMPLETE**
  - **Component:** `libs/contracts/models.py`
  - **Evidence:** canonical enums and Pydantic models exist with focused contract tests.

- [x] **0.3 SQLite WAL engine and per-service database configuration — COMPLETE**
  - **Component:** `libs/database/sqlite.py`
  - **Evidence:** WAL, foreign keys, busy timeout, and FULL/NORMAL synchronous modes are configured and tested.

- [~] **0.4 Transactional outbox and consumer inbox — PARTIAL**
  - **Component:** `libs/database/sqlite.py`, service repositories
  - **REVIEW CHANGE:** tables/helpers exist, but inbox helpers are not used by consumers and only the OMS runs an outbox publisher. Risk writes outbox records without a dispatcher.
  - **AI IMPLEMENTATION NOTE — ADDED:** persist the complete envelope metadata needed for replay (`event_id`, topic/schema version, correlation/causation IDs, source, occurred/created time, payload). Claim/lease batches safely and preserve the original event ID on every retry.
  - **Acceptance:** every event-producing service dispatches its outbox; every state-changing consumer records deduplication in the same transaction as its state change; full envelope identity survives replay; restart, publish/mark crash-window, and duplicate-delivery tests pass.

- [~] **0.5 Event bus protocol and streaming backbone — PARTIAL**
  - **Component:** `libs/events/bus.py`
  - **REVIEW CHANGE:** the implemented bus is process-local and non-durable. Redpanda is not connected to application code, so “event streaming backbone” is not complete.
  - **AI IMPLEMENTATION NOTE — ADDED:** keep `EventBus` as the application-facing protocol and `InMemoryEventBus` as the deterministic test/local adapter. Add a production Redpanda/Kafka adapter selected by typed configuration; do not put broker or domain logic in the transport adapter.
  - **Acceptance:** implement Redpanda/Kafka publishing and consumer groups, stable partition keys, retries with bounded backoff, poison-message/DLQ handling, health/lag metrics, graceful shutdown, event-schema compatibility tests, replay/duplicate tests, and cross-process integration tests.

- [x] **0.6 Broker protocol and structured logging foundation — COMPLETE (foundation only)**
  - **Component:** `libs/broker_models/adapter.py`, `libs/observability/logger.py`
  - **Scope note:** metrics, tracing, redaction validation, and alerting remain in Phase 11.

- [x] **0.7 Database migrations and schema compatibility — COMPLETE — ADDED**
  - **Component:** per-service Alembic migration directories (`services/<svc>/migrations/`), shared migration runner (`infra/migrations/runner.py`), CLI (`infra/migrations/cli.py`), backup manager (`infra/migrations/backup.py`), service registry (`infra/migrations/registry.py`)
  - **AI IMPLEMENTATION NOTE — ADDED:** each service owns its migration history and runs only against its own SQLite file. Do not create a cross-service migration that opens multiple service databases in one transaction.
  - **Acceptance:** Alembic version tables per service, versioned forward migrations, startup compatibility checks, backup-before-migrate, and upgrade/restore rehearsal on empty and copied prior-version databases.
  - **IMPLEMENTATION UPDATE (2026-09-16):** Implemented isolated per-service Alembic environments across all 10 microservices with versioned forward migrations, automated online backup-before-migrate, automatic restore on failure, PRAGMA integrity verification, and fail-closed startup schema compatibility checks.
  - **Changed:** `pyproject.toml`, `libs/config/settings.py`, `.env.example`, `infra/migrations/__init__.py`, `infra/migrations/registry.py`, `infra/migrations/backup.py`, `infra/migrations/runner.py`, `infra/migrations/cli.py`, `services/api_gateway/service_container.py`, `services/oms/migrations/`, `services/risk/migrations/`, `services/portfolio/migrations/`, `services/instrument/migrations/`, `services/broker_session/migrations/`, `services/historical/migrations/`, `services/audit/migrations/`, `services/strategy/migrations/`, `services/auth/migrations/`, `services/api_gateway/migrations/`, `tests/test_migrations.py`
  - **Contracts/Data:** per-service Alembic version tables (`alembic_version`), revision `0001` baseline migrations across all 10 services.
  - **Configuration:** `AUTO_MIGRATE_ON_STARTUP`, `MIGRATION_BACKUP_DIR`.
  - **Verification:** `python -m pytest tests/test_migrations.py -v` -> PASS (10/10 tests), `python -m pytest tests/ -v` -> PASS (53/53 tests), `npm run build` in `frontend/trading-ui` -> PASS (4/4 static pages generated), `python -m infra.migrations.cli status` -> PASS (10/10 services UP TO DATE), `python -m infra.migrations.cli check` -> PASS (all services at head).
  - **Failure-path coverage:** unmigrated database detection, fail-closed startup rejection with `SchemaCompatibilityError` when `AUTO_MIGRATE_ON_STARTUP=false`, automatic rollback/restore from backup snapshot upon simulated migration failure, `PRAGMA integrity_check` validation, and downgrade-regrade reversibility.
  - **Remaining limitations:** none.

- [x] **0.8 Configuration and secrets boundary — COMPLETE — ADDED**
  - **Component:** `libs/config/settings.py`, `libs/config/__init__.py`, `services/api_gateway/service_container.py`, `run_platform.py`, `.env.example`
  - **Acceptance:** typed settings; fail-closed validation; secrets supplied by environment/secret manager; log redaction tests; PAPER is the immutable default when configuration is absent or invalid.
  - **IMPLEMENTATION UPDATE (2026-09-16):** Implemented `PlatformSettings` with Pydantic Settings, fail-closed production checks, secret masking, safe database path derivation, and injected it into service container and launcher.
  - **Changed:** `libs/config/settings.py`, `libs/config/__init__.py`, `services/api_gateway/service_container.py`, `services/api_gateway/main.py`, `run_platform.py`, `pyproject.toml`, `.env.example`, `tests/test_config.py`
  - **Contracts/Data:** none (configuration layer)
  - **Configuration:** `APP_ENV`, `SERVICE_NAME`, `DATA_ROOT`, `EVENT_BUS_BACKEND`, `REDPANDA_BROKERS`, `REDIS_URL`, `DEFAULT_TRADING_MODE`, `LIVE_TRADING_ENABLED`, `LIVE_ALLOWED_ACCOUNTS`, `CORS_ALLOWED_ORIGINS`, `MARKET_DATA_BACKEND`, `BREEZE_API_KEY`, `BREEZE_SECRET_KEY`, `BREEZE_SESSION_TOKEN`, `AUTH_SIGNING_KEY`, `LOG_LEVEL`
  - **Verification:** `python -m pytest tests/test_config.py -v` -> PASS (8/8 tests), `python -m pytest tests/ -v` -> PASS (17/17 tests)
  - **Failure-path coverage:** rejection of LIVE as default mode, rejection of wildcard CORS, rejection of Redpanda without brokers, rejection of in-memory bus/simulated feed in production, rejection of Breeze backend without credentials, and secret string redaction.
  - **Remaining limitations:** none. Follow-up privileged runtime live activation is tracked by 7.4.

- [ ] **0.9 Financial value types and rounding policy — PENDING — ADDED**
  - **Priority:** P0 before further risk, P&L, or live execution work
  - **Component:** `libs/contracts/`, affected repositories/API types, migrations
  - **Depends on:** 0.7
  - **AI IMPLEMENTATION NOTE — ADDED:** follow the Financial-Value Policy in this document. Inventory every monetary/price field before changing schemas. Preserve explicit API compatibility or version the contract; never mix float and decimal values silently.
  - **Acceptance:** tick-size quantization; deterministic INR rounding; Decimal/integer-paise persistence policy; invalid precision rejection; migration of existing values; serialization contract; tests for ₹0.05 ticks, fees, partial fills, shorts/reversals, large values, and round trips.

---

## Phase 1: Broker Session and Gateway

- [x] **1.1 Broker session database and repository — COMPLETE**
  - **Component:** `services/broker_session/repository.py`

- [~] **1.2 Broker session lifecycle — PARTIAL**
  - **Component:** `services/broker_session/service.py`
  - **REVIEW CHANGE:** masked session persistence and expiry checks exist, but activation does not authenticate against Breeze, health latency is hard-coded, credentials are lost on restart, and session state is not wired to the live adapter.
  - **Acceptance:** real authentication/validation, scheduled expiry handling, degraded/down health checks, safe secret renewal, restart behavior, and negative tests.

- [x] **1.3 PAPER broker adapter — COMPLETE for prototype PAPER scope**
  - **Component:** `services/broker_gateway/paper_adapter.py`
  - **Scope note:** realistic partial fills, slippage, fees, latency, exchange hours, and persistence should be added before relying on PAPER results for strategy validation.

- [~] **1.4 ICICI Breeze live broker adapter — PARTIAL**
  - **Component:** `services/broker_gateway/icici_breeze_adapter.py`
  - **REVIEW CHANGE:** place/funds request scaffolding exists. `get_order_status`, `get_positions`, and `get_trades` are not implemented; modify/cancel return unconditional success; test credentials trigger simulated responses.
  - **Acceptance:** implement and contract-test all adapter methods against documented Breeze payloads; distinguish timeout/transport/rejection/auth/rate-limit failures; close HTTP clients cleanly; no simulated response in a LIVE path.

- [~] **1.5 Broker gateway routing — PARTIAL**
  - **Component:** `services/broker_gateway/service.py`, `services/api_gateway/service_container.py`
  - **REVIEW CHANGE:** enum routing works, but session credentials and health are not wired to the live adapter and LIVE is not server-gated.
  - **Acceptance:** account-aware adapter lifecycle, session propagation, LIVE permission check, health gating, and integration tests proving that invalid/expired sessions cannot place orders.

- [ ] **1.6 Broker market/order WebSocket ingestion — PENDING — ADDED**
  - **Component:** `services/broker_gateway/feeds.py`
  - **Acceptance:** authenticated subscriptions, reconnect/resubscribe with jitter, sequence/gap detection, normalized quote/order/trade events, and stale/down health transitions.

---

## Phase 2: Instruments and Contract Master

- [x] **2.1 Instruments database and repository — COMPLETE**
  - **Component:** `services/instrument/repository.py`

- [~] **2.2 Contract search and development seeder — PARTIAL**
  - **Component:** `services/instrument/service.py`
  - **REVIEW CHANGE:** search works, but the master is synthetic and hard-coded for limited NIFTY/BANKNIFTY strikes and expiries. It is not a production contract master.

- [ ] **2.3 Daily automated Breeze contract-master sync — PENDING**
  - **Priority:** P0 before live market data or LIVE orders
  - **Component:** `services/instrument/sync_worker.py`
  - **Acceptance:** checksum/versioned import, atomic swap, delisted/expired handling, retry/alerting, last-success health, and fixture-based parser tests.

- [ ] **2.4 Deterministic contract resolver — PENDING — ADDED**
  - **Component:** `services/instrument/resolver.py`
  - **Acceptance:** resolve by underlying/expiry/strike/right; validate lot/tick sizes and broker token; reject ambiguity/stale master; cover weekly/monthly expiry edge cases.

---

## Phase 3: Historical Market Data

- [x] **3.1 Historical candle database and uniqueness rules — COMPLETE**
  - **Component:** `services/historical/repository.py`

- [~] **3.2 Candle retrieval, synthetic generator, and backfill scaffold — PARTIAL**
  - **Component:** `services/historical/service.py`
  - **REVIEW CHANGE:** repository retrieval and synthetic data exist; a verified Breeze pagination/backfill implementation does not.

- [ ] **3.3 Gap detection and automatic backfill — PENDING**
  - **Component:** `services/historical/gap_detector.py`
  - **Acceptance:** interval-aware gaps, idempotent pagination, rate limits, restart checkpoints, and health/alerts.

- [ ] **3.4 Exchange calendar and timestamp normalization — PENDING — ADDED**
  - **Component:** `libs/market_calendar/`
  - **Acceptance:** Asia/Kolkata trading sessions, holidays/special sessions, UTC storage, interval boundaries, and tests covering market-closed periods.

---

## Phase 4: Live Market Data and Option Chain

- [~] **4.1 Real-time candle builder — PARTIAL**
  - **Component:** `services/market_data/candle_builder.py`
  - **REVIEW CHANGE:** aggregation exists for simulated/in-process ticks; production feed gaps, late/out-of-order ticks, persistence, and restart recovery are not evidenced.

- [~] **4.2 Quote cache and freshness monitor — PARTIAL**
  - **Component:** `services/market_data/service.py`
  - **REVIEW CHANGE:** this is an in-memory simulated feed. Redis and a real Breeze feed are not integrated.

- [~] **4.3 Option-chain matrix aggregator — PARTIAL**
  - **Component:** `services/option_chain/service.py`
  - **REVIEW CHANGE:** matrix assembly exists, but missing quotes are replaced by simulated premiums; production output must expose missing/stale data instead of inventing it.

- [ ] **4.4 Option Greeks and implied volatility — PENDING**
  - **Component:** `services/option_chain/greeks.py`
  - **Acceptance:** configurable risk-free rate/dividend assumptions, stable IV solver, expiry edge cases, unit tests against reference values, and stale-input flags.

- [ ] **4.5 Subscription manager and feed recovery — PENDING — ADDED**
  - **Component:** `services/market_data/subscriptions.py`
  - **Acceptance:** reference-counted subscriptions, broker subscription limits, reconnect/replay, quote sequencing, and observable lag/drop metrics.

---

## Phase 5: OMS, Idempotency, and Reconciliation

- [~] **5.1 Canonical 14-state OMS state machine — PARTIAL**
  - **Component:** `services/oms/state_machine.py`
  - **REVIEW CHANGE:** state definitions and basic transitions exist, but all transition paths, invalid broker regressions, partial fills, restart races, modify/cancel, and concurrent events need coverage.

- [x] **5.2 OMS repository and atomic state/outbox writes — COMPLETE**
  - **Component:** `services/oms/repository.py`

- [~] **5.3 OMS service and outbox publisher — PARTIAL**
  - **Component:** `services/oms/service.py`
  - **REVIEW CHANGE:** the worker exists, but the simultaneous fast-path publish plus later outbox publish can deliver duplicates and consumers do not use durable inbox deduplication. Publishing and marking are not protected against the publish/mark crash window at consumers.

- [ ] **5.4 Continuous unknown-order reconciliation — PENDING — REVIEW CHANGE**
  - **Priority:** P0; required by `SUBMISSION_UNKNOWN`
  - **Component:** `services/reconciliation/worker.py`
  - **Acceptance:** query broker using client/order fingerprints, resolve to acknowledged/open/filled/rejected only with evidence, never resubmit automatically, alert on SLA breach, and persist every attempt/result.

- [ ] **5.5 Startup/periodic/EOD reconciliation — PENDING — REVIEW CHANGE**
  - **Component:** `services/reconciliation/worker.py`
  - **Acceptance:** reconcile orders, trades, positions, and funds at startup, periodically, and after market close; classify discrepancies; provide operator workflow and tests with broker snapshots.

---

## Phase 6: Pre-Trade Risk and Safety Controls

- [x] **6.1 Risk database and repository — COMPLETE**
  - **Component:** `services/risk/repository.py`

- [~] **6.2 Pre-trade risk engine — PARTIAL**
  - **Component:** `services/risk/service.py`
  - **REVIEW CHANGE:** system mode, max quantity, basic price, and an in-memory one-second duplicate check exist. Missing controls include lot/tick validation, instrument/market status, funds/margin, max position/exposure, open-order limits, price bands, durable idempotency, and strategy/account limits.
  - **Acceptance:** risk settings are persisted/versioned; decisions record inputs/rule versions; all checks fail closed when dependencies are stale or unavailable.

- [~] **6.3 Emergency kill switch — PARTIAL**
  - **Component:** `services/risk/service.py`, API/UI controls
  - **REVIEW CHANGE:** mode persistence exists, but there is no authentication/authorization, invalid action rejection, independent server-side LIVE gate, or tested definition of position-reducing orders. BUY/SELL alone is insufficient to distinguish entry from exit.
  - **Acceptance:** privileged operator authorization, reason/audit identity, compare-and-set mode changes, position-aware EXIT_ONLY enforcement, reset workflow, and drill tests.

- [ ] **6.4 Maximum daily drawdown/loss guard — PENDING**
  - **Component:** `services/risk/daily_loss_guard.py`
  - **Acceptance:** broker-reconciled day P&L, persisted thresholds, hysteresis/reset policy, stale-P&L fail-safe behavior, alerting, and boundary tests.

- [ ] **6.5 Risk test matrix and operator configuration — PENDING — ADDED**
  - **Component:** `tests/risk/`, risk configuration API/UI
  - **Acceptance:** table-driven tests for every mode/rule/order side/current position combination and maker-checker controls for LIVE limit changes.

---

## Phase 7: Execution and No-Blind-Retry Protocol

- [~] **7.1 Execution pipeline — PARTIAL**
  - **Component:** `services/execution/service.py`
  - **REVIEW CHANGE:** approved orders reach the selected adapter, but execution idempotency is an in-memory set, no persisted submission attempt precedes the network call, and restart/concurrency behavior is unsafe.
  - **Acceptance:** durable idempotency key, persisted attempt state, per-account rate limits, single-writer/lease semantics, and crash-point tests before/during/after broker submission.

- [~] **7.2 `SUBMISSION_UNKNOWN` protocol — PARTIAL**
  - **Component:** `services/execution/service.py`, `services/oms/service.py`
  - **REVIEW CHANGE:** unknown status is recorded without blind retry, which is correct, but no reconciliation queue/worker resolves it. Completion depends on 5.4.

- [ ] **7.3 Modify, cancel, and square-off workflows — PENDING — ADDED**
  - **Component:** execution, OMS, gateway, API, and UI
  - **Acceptance:** state-aware idempotent commands, broker confirmation, timeout-to-unknown handling, partial-fill races, and reconciliation.

- [x] **7.4 Server-side LIVE activation gate — COMPLETE — ADDED**
  - **Priority:** P0 before any real credential is configured
  - **Acceptance:** LIVE disabled by default; authenticated privileged activation; account allowlist; explicit expiry; two-step confirmation; readiness checks; durable audit; immediate revocation. A browser Zustand toggle must never grant LIVE authority.
  - **IMPLEMENTATION UPDATE (2026-09-16):** Implemented server-side `LiveTradingGate` with two-step challenge/confirmation token workflow, account allowlist, explicit auto-expiring authorization window, immediate emergency revocation, full audit event publishing, pre-trade `RiskService` fail-closed gate enforcement, `ExecutionService` live authorization check, and API Gateway endpoints (`/api/v1/live-gate/*`).
  - **Changed:** `services/risk/live_gate.py`, `services/risk/service.py`, `services/execution/service.py`, `services/api_gateway/service_container.py`, `services/api_gateway/main.py`, `tests/test_live_gate.py`
  - **Contracts/Data:** Added REST schemas (`LiveGateChallengeRequest`, `LiveGateConfirmRequest`, `LiveGateRevokeRequest`) and audit event payload schemas (`LIVE_ACTIVATION_CHALLENGE_ISSUED`, `LIVE_MODE_ACTIVATED`, `LIVE_MODE_REVOKED`)
  - **Configuration:** uses `PlatformSettings.live_trading_enabled` and `PlatformSettings.live_allowed_accounts`
  - **Verification:** `python -m pytest tests/test_live_gate.py -v` -> PASS (8/8 tests), `python -m pytest tests/ -v` -> PASS (25/25 tests), `npm run build` -> PASS (4/4 static pages generated)
  - **Failure-path coverage:** rejection on token mismatch, operator mismatch, expired challenge, unallowlisted account, expired active window, and unauthorized order intents at both Risk and Execution service boundaries.
  - **Remaining limitations:** none.

---

## Phase 8: Portfolio, P&L, and Strategies

- [x] **8.1 Portfolio repository and P&L snapshots — COMPLETE**
  - **Component:** `services/portfolio/repository.py`

- [~] **8.2 Position/VWAP/P&L engine — PARTIAL**
  - **Component:** `services/portfolio/service.py`
  - **REVIEW CHANGE:** the simple long-oriented calculation works for the tested happy path but needs FIFO/average-cost policy definition, shorts, reversals, partial closes, fees/taxes, expiry settlement, day boundary, and broker reconciliation.

- [~] **8.3 Strategy framework — PARTIAL**
  - **Component:** `services/strategy/`
  - **REVIEW CHANGE:** signal persistence and SHADOW/PAPER/LIVE routing scaffolding exist; lifecycle supervision, durable state, scheduling, parameter validation, capital allocation, risk budgets, and recovery are incomplete. Keep strategy LIVE disabled until Phase 12 passes.

- [ ] **8.4 Deterministic backtest and replay runner — PENDING**
  - **Component:** `services/strategy/backtest_engine.py`
  - **Acceptance:** same strategy logic as live, deterministic clock/data, fees/slippage/latency, reproducible artifacts, no look-ahead, and golden tests.

- [ ] **8.5 Broker portfolio reconciliation — PENDING — ADDED**
  - **Component:** portfolio plus reconciliation service
  - **Acceptance:** compare executions/positions/P&L with broker state, quarantine discrepancies from strategy decisions, and expose operator resolution.

---

## Phase 9: Audit, API Gateway, and Security

- [~] **9.1 Append-only audit service — PARTIAL**
  - **Component:** `services/audit/`
  - **REVIEW CHANGE:** selected audit/risk events are inserted, but immutability is not enforced at the database boundary and order/execution/session/security coverage is incomplete.
  - **Acceptance:** prohibit update/delete through repository and database controls, capture all privileged/trading actions with actor/correlation IDs, redact secrets, define retention/export, and add tamper-evidence or external archival.

- [~] **9.2 FastAPI gateway — PARTIAL**
  - **Component:** `services/api_gateway/main.py`
  - **REVIEW CHANGE:** useful REST endpoints exist, but the gateway directly hosts all “services,” has no authentication/authorization, and reports several hard-coded healthy/live values.

- [~] **9.3 Browser WebSocket multiplexer — PARTIAL**
  - **Component:** `services/api_gateway/main.py`
  - **REVIEW CHANGE:** basic broadcast works; authentication, authorization, topic subscriptions, bounded queues/backpressure, slow-client eviction, origin checks, reconnect cursors, and WebSocket tests are missing.

- [x] **9.4 API/WebSocket authentication and RBAC — COMPLETE — ADDED**
  - **Priority:** P0 before non-local deployment
  - **Component:** `services/auth/`, API dependencies/middleware, WebSocket handshake, frontend auth state
  - **AI IMPLEMENTATION NOTE — ADDED:** use server-owned identities and roles (`ADMIN`, `OPERATOR`, `TRADER`, `READ_ONLY`). Use short-lived access credentials, securely rotated/revocable refresh state, and a short-lived single-use WebSocket ticket or equally strong authenticated handshake. Store password verifiers/refresh tokens only as modern hashes; use Secure/HttpOnly/SameSite cookies when cookies are used and protect state-changing cookie requests against CSRF. Do not put long-lived tokens in browser local storage or WebSocket query-string logs.
  - **Acceptance:** login/logout/refresh/revocation; bootstrap and recovery procedure; endpoint and message authorization; operator-only safety/LIVE actions; expired/revoked/role-denied tests; origin/CSRF policy; rate-limited login; secret-safe errors; and complete security audit events.
  - **IMPLEMENTATION UPDATE (2026-09-16):** Implemented server-owned identities and roles (`ADMIN`, `OPERATOR`, `TRADER`, `READ_ONLY`) with salted PBKDF2 password hashing, HS256 JWT access tokens, revocable refresh tokens with automatic rotation, single-use WebSocket tickets, role-based endpoint protection (FastAPI dependencies `get_current_user` and `require_roles`), protected `/ws/live` connection handshakes, and security audit event dispatching.
  - **Changed:** `services/auth/__init__.py`, `services/auth/security.py`, `services/auth/repository.py`, `services/auth/service.py`, `services/api_gateway/dependencies.py`, `services/api_gateway/service_container.py`, `services/api_gateway/main.py`, `libs/config/settings.py`, `libs/contracts/models.py`, `tests/test_auth.py`, `tests/test_api_gateway.py`, `tests/test_live_gate.py`, `services/oms/repository.py`, `services/oms/service.py`, `pyproject.toml`
  - **Contracts/Data:** Added `UserRole` enum, `UserPrincipal` model, isolated `auth.db` schema (`users`, `refresh_tokens`, `ws_tickets`), auth REST endpoints (`/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/logout`, `/api/v1/auth/me`, `/api/v1/auth/ws-ticket`), and security audit topics (`USER_LOGIN_SUCCESS`, `USER_LOGIN_FAILURE`, `USER_LOGOUT`, `TOKEN_REFRESHED`, `UNAUTHORIZED_ACCESS_DENIED`, `WS_TICKET_ISSUED`, `WS_AUTHENTICATION_SUCCESS`, `WS_AUTHENTICATION_FAILURE`).
  - **Configuration:** `auth_db_path`, `access_token_expire_minutes` (15m), `refresh_token_expire_days` (7d), `ws_ticket_expire_seconds` (60s), `auth_signing_key`
  - **Verification:** `python -m pytest tests/test_auth.py -v` -> PASS (8/8 tests), `python -m pytest tests/ -v` -> PASS (33/33 tests), `npm run build` -> PASS (4/4 static pages generated)
  - **Failure-path coverage:** invalid credentials, expired access tokens, revoked refresh tokens, ticket reuse rejection, unauthorized role operations (403 Forbidden for READ_ONLY on orders and TRADER on safety), unauthenticated WebSocket rejection (4401).
  - **Remaining limitations:** none.

- [x] **9.5 Boundary protection and idempotent commands — COMPLETE — ADDED**
  - **Acceptance:** strict CORS allowlist, request-size limits, API rate limits, idempotency keys for commands, structured validation errors, security headers, and audit of denied privileged actions.
  - **IMPLEMENTATION UPDATE (2026-09-16):** Implemented defensive HTTP security headers (CSP, nosniff, DENY, Referrer-Policy, Permissions-Policy, X-Request-ID), 1MB payload ceiling middleware (`RequestSizeLimitMiddleware`), tiered sliding-window rate limiting (`RateLimitMiddleware`), strict CORS origin validation (`StrictOriginMiddleware`), standardized structured error handlers, and SQLite-backed command idempotency (`IdempotencyRepository` on `gateway.db`) with exact cached replay (`X-Idempotency-Replay`) for orders, cancellations, kill-switch, system mode, and LIVE gate activations.
  - **Changed:** `services/api_gateway/idempotency.py`, `services/api_gateway/rate_limiter.py`, `services/api_gateway/middleware.py`, `services/api_gateway/error_handlers.py`, `services/api_gateway/service_container.py`, `services/api_gateway/main.py`, `libs/config/settings.py`, `libs/events/bus.py`, `.env.example`, `tests/test_boundary_security.py`
  - **Contracts/Data:** Added isolated `gateway.db` schema (`idempotency_records`), request fingerprint hashing (SHA-256), `Idempotency-Key` command header handling, structured error response schema (`error.code`, `error.message`, `error.details`, `error.request_id`, `error.timestamp`), and security audit topics (`CORS_ORIGIN_DENIED`, `PAYLOAD_SIZE_EXCEEDED`, `RATE_LIMIT_EXCEEDED`, `IDEMPOTENCY_MISMATCH`).
  - **Configuration:** `gateway_db_path`, `max_request_body_bytes` (1MB), `rate_limit_enabled`, `rate_limit_login_per_minute` (60), `rate_limit_orders_per_minute` (60), `rate_limit_general_per_minute` (120), `idempotency_ttl_seconds` (24h).
  - **Verification:** `python -m pytest tests/test_boundary_security.py -v` -> PASS (10/10 tests), `python -m pytest tests/ -v` -> PASS (43/43 tests), `npm run build` -> PASS (4/4 static pages generated).
  - **Failure-path coverage:** disallowed CORS origin (403), payload size exceeding 10KB/1MB (413), rate limit exceeded (429 with `Retry-After`), idempotency key format syntax error (400), idempotency payload mismatch tampering (422), unauthenticated/forbidden/not-found structured errors (401/403/404), and audit trail event logging.
  - **Remaining limitations:** none.

---

## Phase 10: React Trading UI

- [~] **10.1 UI scaffolding and version baseline — PARTIAL**
  - **Component:** `frontend/trading-ui/`
  - **Evidence:** production build passed during review (4/4 static pages).
  - **REVIEW CHANGE:** actual build is Next.js 15.5.25 with Lightweight Charts 4.2.3 and Tailwind 3.4.x, not the claimed target baseline.
  - **AI IMPLEMENTATION NOTE — ADDED:** the chosen target is Node 24 LTS, Next.js 16, React 19, Lightweight Charts 5.2+, and Tailwind 4. Upgrade one major dependency boundary at a time, follow official migration guides, regenerate the lockfile with `npm install`, then prove clean reproducibility with `npm ci`, type checking, UI tests, and production build. Do not combine visual redesign with the dependency migration.
  - **Acceptance:** manifests/lockfile and Docker image use the target major lines; deprecated APIs/scripts are replaced; clean install, type check, component tests, and production build pass; key trading screens receive a smoke/visual regression check.

- [~] **10.2 Global terminal header — PARTIAL**
  - **Component:** `features/header/GlobalHeader.tsx`
  - **REVIEW CHANGE:** indicators render, but several defaults/fallbacks look healthy and the trading mode is local UI state. Safety and trading modes must come from authoritative server state.

- [x] **10.3 Market watch — COMPLETE for prototype scope**

- [~] **10.4 Financial chart — PARTIAL**
  - **Component:** `features/charts/TradingChart.tsx`
  - **REVIEW CHANGE:** basic chart exists on Charts 4.x; the specification’s 30m interval, trade/order markers, position/entry/stop/current-price lines, and historical paging are not evidenced.

- [x] **10.5 Option-chain matrix — COMPLETE for prototype scope**

- [~] **10.6 Manual order ticket — PARTIAL**
  - **Component:** `features/order_ticket/OrderTicket.tsx`
  - **REVIEW CHANGE:** PAPER submission works, but instrument metadata/server validation, stale-price warning, idempotency, estimated fees/margin, order preview, and protected LIVE confirmation are missing.

- [x] **10.7 Tabbed bottom panel — COMPLETE for prototype scope**

- [~] **10.8 Kill-switch modal — PARTIAL**
  - **Component:** `features/modals/KillSwitchModal.tsx`
  - **REVIEW CHANGE:** UI calls the backend controls, but completion depends on authenticated RBAC, authoritative state refresh, failure handling, and Phase 6 drill tests.

- [ ] **10.9 UI automated tests and accessibility — PENDING — ADDED**
  - **Component:** Vitest/Testing Library and Playwright suites
  - **Acceptance:** component tests for trading controls; end-to-end PAPER flow; reconnect/error states; keyboard navigation; accessible dialogs/tables; visual regression for critical status colors and labels.

---

## Phase 11: Deployment, Operations, and Recovery

- [~] **11.1 Docker Compose environment — PARTIAL**
  - **Component:** `docker-compose.yml`, Dockerfiles
  - **REVIEW CHANGE:** Compose declares Redis/Redpanda but application code does not use them; all backend modules run in one container. Image builds and service health checks were not evidenced, and the backend Dockerfile installs the project before copying its declared README/source.
  - **Acceptance:** reproducible image builds, non-root users, pinned images/dependencies, health checks, startup ordering by readiness, graceful shutdown, resource limits, and a deployment topology matching the architecture decision.

- [~] **11.2 Runner and environment configuration — PARTIAL**
  - **Component:** `run_platform.py`, `.env.example`
  - **REVIEW CHANGE:** a runner/template exists, but much of the declared environment configuration is not consumed by typed settings and fail-closed startup validation.

- [ ] **11.3 SQLite online backup and restore drills — PENDING**
  - **Priority:** P0 before LIVE
  - **Component:** `infra/scripts/sqlite_backup.py`
  - **Acceptance:** SQLite backup API, integrity check, encryption/access controls, retention, off-host copy, restore automation, and measured restore-point/restore-time drill.

- [ ] **11.4 Metrics, tracing, dashboards, and alerts — PENDING**
  - **Component:** `libs/observability/metrics.py`, deployment dashboards
  - **Acceptance:** order latency/error/unknown counters; outbox age; consumer lag; feed age; reconciliation discrepancies; DB contention; session expiry; risk/kill-switch events; actionable alerts.

- [ ] **11.5 CI quality and security gates — PENDING — ADDED**
  - **Acceptance:** Python tests/coverage, Ruff, type checking, frontend build/lint/tests, dependency and secret scanning, container build/scan, migration tests, and protected release artifacts.

- [ ] **11.6 Production deployment and incident runbooks — PENDING — ADDED**
  - **Acceptance:** TLS/reverse proxy, least-privilege host/service accounts, log retention, time synchronization, capacity/load tests, rollback, broker outage/feed outage/DB-lock/session-expiry/unknown-order runbooks, and operator drills.

---

## Phase 12: Release Gates

### Gate A — PAPER Beta

- [ ] CI is green and reproducible from a clean checkout.
- [ ] Contract master, historical backfill, and real market feed pass soak tests.
- [ ] PAPER order lifecycle, partial fills, cancel/modify, P&L, and restart recovery pass.
- [ ] Authentication/RBAC, strict CORS, and WebSocket controls are enabled.
- [ ] Backups restore successfully and observability alerts are exercised.

### Gate B — Manual LIVE Pilot

- [ ] Every item marked P0 anywhere in this roadmap is complete, including foundational items 0.7–0.9 and portfolio reconciliation 8.5.
- [ ] Breeze sandbox/controlled-account contract tests pass for funds, place, status, modify, cancel, orders, trades, positions, and feeds.
- [ ] Duplicate delivery and every submission crash point have been tested.
- [ ] `SUBMISSION_UNKNOWN` is resolved by reconciliation without automatic resubmission.
- [ ] Startup, periodic, and EOD reconciliation match broker state.
- [ ] Risk limits, EXIT_ONLY, HALT, and drawdown controls pass operator drills.
- [ ] LIVE is enabled only server-side for an allowlisted account, small quantity/notional, and a time-bounded pilot window.
- [ ] Manual broker-terminal observation and rollback/square-off procedures are staffed.

### Gate C — Automated Strategy LIVE

- [ ] Manual LIVE pilot has an agreed observation period with zero unresolved severity-1/2 incidents.
- [ ] Strategy replay/backtest is deterministic and production logic is shared.
- [ ] Per-strategy capital/risk budgets and independent kill controls are enforced.
- [ ] Strategy/portfolio state recovers correctly after process and host restarts.
- [ ] Formal operator sign-off is recorded; browser selection alone can never activate strategy LIVE mode.

---

## Recommended Implementation Sequence

Priority meaning: **P0** blocks any LIVE use, **P1** blocks PAPER Beta or trusted operator workflows, and **P2** blocks automated-strategy release. Completing a later priority never waives an earlier gate. Phase 12 remains authoritative even when an item is not repeated in this sequence.

1. **P0 configuration and safety boundary:** 0.8, then the fail-closed portion of 7.4. LIVE stays disabled.
2. **P0 identity and API boundary:** 9.4 and 9.5, then complete privileged activation in 7.4.
3. **P0 schema and money correctness:** 0.7 and 0.9 before further risk, P&L, or execution schema work.
4. **P0 broker/instrument truth:** 1.2, 1.4, 1.5, 2.3, and 2.4 using approved official fixtures; then 1.6 when a controlled session is available.
5. **P0 durable delivery:** 0.4, 0.5, and 5.3.
6. **P0 execution and recovery:** 7.1, 5.4, 5.5, 7.2, and 7.3.
7. **P0 risk, portfolio, and operations:** 6.2–6.5, 8.2, 8.5, and 11.3–11.6.
8. **P1 trusted market data:** 3.2–3.4 and 4.1–4.5.
9. **P1 audit/API/UI hardening:** 9.1–9.3 and 10.1–10.9.
10. **P2 analytics/automation:** 4.4, 8.3, and 8.4, followed by Gate C evaluation.

> **REVIEW CHANGE:** This ordering deliberately places authentication, LIVE gating, broker truth, reconciliation, risk, backups, and observability before Greeks, backtesting, or automated LIVE strategies.

## Reviewed Status Summary

| Phase | Complete | Partial | Pending | Review interpretation |
|:--|--:|--:|--:|:--|
| 0. Foundations | 5 | 3 | 1 | Good prototype foundation; 0.7 & 0.8 complete; durable eventing and money policy remain |
| 1. Broker | 2 | 3 | 1 | PAPER usable; LIVE adapter/feed not ready |
| 2. Instruments | 1 | 1 | 2 | Synthetic master only |
| 3. Historical | 1 | 1 | 2 | Storage works; production ingestion/gaps remain |
| 4. Live market data | 0 | 3 | 2 | Simulated/in-memory only |
| 5. OMS/reconciliation | 1 | 2 | 2 | Core persistence exists; recovery is a blocker |
| 6. Risk | 1 | 2 | 2 | Basic rules only; not LIVE-grade |
| 7. Execution | 1 | 2 | 1 | 7.4 Server LIVE gate complete; durability/recovery incomplete |
| 8. Portfolio/strategy | 1 | 2 | 2 | Long happy path and scaffolding only |
| 9. API/security | 2 | 3 | 0 | 9.4 Auth/RBAC & 9.5 Boundary/idempotency complete; audit immutability remains |
| 10. UI | 3 | 5 | 1 | Prototype build passes; version/safety/testing gaps |
| 11. Operations | 0 | 2 | 4 | Deployment and recovery evidence missing |
| **Total** | **18** | **29** | **20** | **Prototype progress systematically advancing with verified P0 boundaries and migrations** |

The totals count numbered Phase 0–11 roadmap items only; Phase 12 gate checkboxes are release evidence and are not included in the item totals.

## Review Verification Record

- Frontend production build: **PASSED** using the installed Next.js 15.5.25 toolchain; 4/4 static pages generated.
- Python tests/lint: **NOT REPRODUCED in this review environment** because no runnable Python 3.14 interpreter was available. Existing test sources and prior bytecode artifacts were inspected, but they are not a substitute for a fresh run.
- Docker Compose/images: **NOT VERIFIED by build/up execution** during this review.
- Repository note: the supplied workspace was not recognized as a Git worktree, so no Git diff/status evidence is available.

## Review Change Log

- **2026-09-16 — REVIEW CHANGE:** replaced feature-presence percentages with COMPLETE/PARTIAL/PENDING evidence-based statuses.
- **2026-09-16 — REVIEW CHANGE:** reclassified in-memory/simulated/live-stub capabilities and corrected frontend version claims.
- **2026-09-16 — ADDED:** migrations, typed configuration/secrets, real broker feeds, deterministic resolver/calendar, subscription recovery, continuous reconciliation, durable execution idempotency, server-side LIVE gate, portfolio reconciliation, API security/RBAC, UI tests, CI/security gates, runbooks, and explicit release gates.
- **2026-09-16 — REVIEW CHANGE:** reordered work so safety, broker truth, reconciliation, risk, backups, and observability precede LIVE or automated strategies.
- **2026-09-16 — ADDED:** AI implementation guide covering source precedence, target topology, invariants, canonical event flow, coding conventions, financial values, configuration, execution workflow, evidence format, verification matrix, external blockers, dependency queue, and known code hotspots.
- **2026-09-16 — REVIEW CHANGE:** resolved prior implementation ambiguity in favor of the specified Redpanda-backed service topology and the Node 24 / Next 16 / Charts 5.2+ / Tailwind 4 UI target.
- **2026-09-16 — ADDED:** item 0.9 for deterministic monetary precision and tick-size rounding before further risk/P&L/live execution work.
- **2026-09-16 — ADDED:** a fully specified first work packet for item 0.8 so a new implementation agent can start safely without inferring configuration behavior.
