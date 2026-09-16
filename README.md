# ICICI Direct Options Trading Platform — Microservices Architecture v2

An enterprise-grade, event-driven options trading platform built around ICICI Direct Breeze APIs using Python 3.14, FastAPI, React 19 / Next.js 16, and isolated SQLite-first transactional durability with WAL mode.

---

## 🏛️ Architecture Overview

The platform is structured according to **Clean Architecture**, **Domain-Driven Design (DDD)**, and **Event-Driven Microservices Principles**:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   Next.js 16 / React 19 Trading UI                    │
│      Dark-Mode Professional Terminal · TradingView Lightweight Charts  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ REST + WebSocket (/ws/live)
┌───────────────────────────────────▼────────────────────────────────────┐
│                       FastAPI API Gateway (BFF)                        │
└─────┬─────────────────────────────┬──────────────────────────────┬─────┘
      │                             │                              │
┌─────▼──────────────┐       ┌──────▼─────────────┐        ┌───────▼─────────────┐
│  Broker Services   │       │   Market Services  │        │   Trading Services  │
│  ───────────────   │       │   ───────────────  │        │   ────────────────  │
│  Broker Session    │       │   Instrument       │        │   OMS (14-state)    │
│  Broker Gateway    │       │   Market Data      │        │   Pre-trade Risk    │
│  (Breeze / Paper)  │       │   Historical Data  │        │   Execution Engine  │
│                    │       │   Option Chain     │        │   Portfolio / P&L   │
│                    │       │                    │        │   Strategy Engine   │
│                    │       │                    │        │   Append-Only Audit │
└─────────┬──────────┘       └──────┬─────────────┘        └───────┬─────────────┘
          │                         │                              │
          └─────────────────────────┼──────────────────────────────┘
                                    │
                         EventBus (Topics v1)
                                    │
           ┌────────────────────────┼────────────────────────┐
           ▼                        ▼                        ▼
  Isolated SQLite DBs      Fast In-Memory / Redis   Redpanda / Kafka
  (WAL + Sync FULL)           Quotes & Caches        Event Streaming
```

---

## 🔒 Key Design Patterns & Engineering Guarantees

1. **Service-Isolated SQLite Databases**:
   - Each state-owning service (`oms`, `portfolio`, `risk`, `strategy`, `broker_session`, `audit`, `instruments`) owns its own SQLite database file under `data/`.
   - Databases are configured with `PRAGMA journal_mode = WAL`, `PRAGMA foreign_keys = ON`, `PRAGMA busy_timeout = 5000`, and `PRAGMA synchronous = FULL` (or `NORMAL` for market data).
2. **Repository Abstraction (`Protocol`)**:
   - Business and domain logic never execute raw SQLite SQL directly. All persistence is managed via repository interfaces (`Protocol`), enabling zero-downtime future migrations to distributed SQL (e.g. PostgreSQL, libSQL) if scale requires it.
3. **Transactional Outbox Pattern**:
   - Order state transitions, risk evaluations, and trade fills are atomically committed with an `outbox_events` record in the same local SQLite transaction (`async with conn: ...`).
   - A background outbox publisher drains the outbox to ensure zero lost messages even during server crash.
4. **Idempotent Consumer Inbox**:
   - Consumers record processed event IDs in `processed_events (event_id, consumer_name)` to protect against duplicate Kafka/EventBus deliveries.
5. **Pre-Trade Risk Engine**:
   - Strategies and UI manual tickets **NEVER** call ICICI Breeze APIs directly.
   - Flow: `Order Intent -> Risk Engine -> Execution Engine -> Broker Gateway`.
   - Pre-trade checks: System Mode (`NORMAL`, `ENTRY_BLOCKED`, `EXIT_ONLY`, `HALTED`), Kill Switch, Max Order Quantity, Lot Size compliance, Duplicate Protection (1-second throttle), and Price Sanity.
6. **No Blind Retries (`SUBMISSION_UNKNOWN`)**:
   - If an order submission to the broker gateway times out or encounters network degradation, the order is automatically marked `SUBMISSION_UNKNOWN` and queued for broker reconciliation; blind resubmissions are strictly prohibited.
7. **UUIDv7 & UTC Timestamps**:
   - Uses native Python 3.14 `uuid.uuid7()` (RFC 9562) for time-sortable, globally unique identifiers across all events, intents, orders, and executions.
   - All timestamps strictly adhere to timezone-aware UTC ISO 8601 format.

---

## 📂 Directory Structure

```text
├── libs/
│   ├── contracts/             # Domain models, enums, UUIDv7 & UTC helpers
│   ├── database/              # SQLite WAL engine, BaseRepository, Outbox & Inbox
│   ├── events/                # Event definitions (v1) and EventBus protocol
│   ├── broker_models/         # BrokerAdapter protocol, ICICI Breeze & Paper adapters
│   └── observability/         # Structured logging, correlation tracking, OpenTelemetry context
├── services/
│   ├── broker_session/        # Session tokens, accounts, funds/margins, broker health
│   ├── broker_gateway/        # Breeze SDK adapter wrapper, rate limiter, paper adapter
│   ├── instrument/            # Contract master, option strike resolver, expiries
│   ├── market_data/           # Live quotes, WebSocket ingestion, candle builder, quote cache
│   ├── historical/            # Breeze historical candle sync, gap detection, candle store
│   ├── option_chain/          # Option chain matrix aggregator (LTP, OI, Greeks, strikes)
│   ├── oms/                   # Order Management System, 14-state machine, outbox
│   ├── portfolio/             # Executions, positions, realized/unrealized P&L snapshots
│   ├── risk/                  # Pre-trade risk checks, kill switches, safety modes
│   ├── execution/             # Order execution worker, SUBMISSION_UNKNOWN recovery
│   ├── strategy/              # Strategy engine (SHADOW, PAPER, LIVE), signals
│   ├── audit/                 # Append-only immutable audit trail
│   └── api_gateway/           # FastAPI BFF, REST routes, /ws/live multiplexer
├── frontend/trading-ui/       # Next.js 16 + React 19 + Tailwind 4 + TradingView Charts
├── tests/                     # Comprehensive test suite (unit, integration, pipeline)
├── data/                      # Dedicated per-service SQLite database directories
├── docker-compose.yml         # Container orchestration (Redis, Redpanda, Services)
└── pyproject.toml             # Python workspace & dependency management
```

---

## 🚀 Quickstart Guide

### 1. Run Automated Test Suite
Ensure all unit, integration, pipeline, and state-machine tests pass:
```bash
python -m pytest tests/ -v
```

### 2. Start the Backend Microservices & API Gateway
Launch the FastAPI API Gateway on port 8000:
```bash
python run_platform.py
```
- API Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- System Health Check: [http://localhost:8000/api/v1/system/health](http://localhost:8000/api/v1/system/health)
- Live WebSocket Stream: `ws://localhost:8000/ws/live`

### 3. Launch the Trading UI
In a separate terminal, start the Next.js 16 / React 19 trading terminal:
```bash
cd frontend/trading-ui
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 🧪 Verified Acceptance Gates

- [x] Python 3.14.7 + Node.js 24 LTS runtime baseline
- [x] Isolated per-service SQLite storage with WAL mode & `PRAGMA synchronous = FULL`
- [x] Transactional Outbox pattern implemented & tested
- [x] Idempotent consumer inbox deduplication tested
- [x] Canonical 14-state OMS state machine
- [x] Pre-trade Risk Engine with System Modes & Kill Switch
- [x] `SUBMISSION_UNKNOWN` protocol without blind retry
- [x] High-fidelity Paper Broker adapter with simulated execution
- [x] ICICI Breeze API adapter with rate-limiting & error normalization
- [x] TradingView Lightweight Charts with live candlestick and volume updates
- [x] Option chain matrix view with one-click order drafting
- [x] Strategy framework supporting `SHADOW`, `PAPER`, and `LIVE` modes
- [x] Append-only audit trail logging
- [x] End-to-end integration test suite passing (100% success rate)

