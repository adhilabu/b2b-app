# DSD B2B SaaS Platform

> **Mobile-first, offline-first SaaS platform for FMCG/CPG Direct Store Delivery (DSD) and Van Sales.**

Built on a polyglot microservices architecture — Go (Orchestration), Python/FastAPI (all domain services) — with JWT RS256 authentication, Apache Pulsar for async events, PostgreSQL per-service databases, and Redis for caching and token management.

---

## 🏗️ Architecture Overview

```
Mobile App (Offline-First)
        │
        ▼ HTTPS
┌─────────────────────────────┐
│   Orchestration Service     │  ← Go/Gin (Port 8000)
│   API Gateway + Sync Broker │
└────────────┬────────────────┘
             │ JWT forwarded
    ┌────────┼────────────────────────────────┐
    ▼        ▼        ▼       ▼      ▼        ▼
Identity  Catalog   Sales   Route  Attendance Notification
 :8001     :8002    :8003   :8004   :8005      :8006
(FastAPI) (FastAPI)(FastAPI)(FastAPI)(FastAPI) (FastAPI)
    │               │       │
    │               └───────┴──► Apache Pulsar (Events)
    ▼
PostgreSQL (6 isolated databases)
Redis (token store, cache)
```

---

## 🚀 Quick Start (5 minutes)

### Prerequisites
- Docker + Docker Compose v2
- `make` (pre-installed on macOS/Linux)
- OpenSSL (for key generation)

### 1. Generate Keys
```bash
make keygen
```
This generates:
- `infra/keys/private.pem` — RSA-4096 private key (Identity service)
- `infra/keys/public.pem` — RSA public key (all other services)
- VAPID keys for Web Push (printed to stdout — add to `.env`)

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env and fill in:
#   POSTGRES_PASSWORD, VAPID keys, Firebase credentials (optional)
```

### 3. Start the Stack
```bash
make up
```

All 7 services + PostgreSQL + Redis + Pulsar start with health checks.

### 4. Verify

```bash
# Check all services are healthy
make ps

# Test the API gateway
curl http://localhost:8000/health

# Login and get a JWT
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "your-password"}'
```

### 5. Interactive API & Developer Docs

#### API Gateway Endpoints
The orchestration gateway on **port 8000** serves as the public ingress for mobile/frontend clients:
- **Root URL**: `http://localhost:8000/` (returns gateway welcome response & downstream service links)
- **Health Check**: `http://localhost:8000/health` (downstream health aggregation check)

#### Downstream Service Documentation
Each Python service automatically generates standard interactive documents and OpenAPI specifications directly on its own port:

| Service | Port | Swagger UI | ReDoc | OpenAPI JSON (for Codegen/Postman) |
|---|---|---|---|---|
| **Orchestration** | `8000` | — | — | — |
| **Identity** | `8001` | [Direct Link](http://localhost:8001/docs) | [ReDoc](http://localhost:8001/redoc) | [openapi.json](http://localhost:8001/openapi.json) |
| **Catalog** | `8002` | [Direct Link](http://localhost:8002/docs) | [ReDoc](http://localhost:8002/redoc) | [openapi.json](http://localhost:8002/openapi.json) |
| **Sales** | `8003` | [Direct Link](http://localhost:8003/docs) | [ReDoc](http://localhost:8003/redoc) | [openapi.json](http://localhost:8003/openapi.json) |
| **Route** | `8004` | [Direct Link](http://localhost:8004/docs) | [ReDoc](http://localhost:8004/redoc) | [openapi.json](http://localhost:8004/openapi.json) |
| **Attendance** | `8005` | [Direct Link](http://localhost:8005/docs) | [ReDoc](http://localhost:8005/redoc) | [openapi.json](http://localhost:8005/openapi.json) |
| **Notification** | `8006` | [Direct Link](http://localhost:8006/docs) | [ReDoc](http://localhost:8006/redoc) | [openapi.json](http://localhost:8006/openapi.json) |

#### Searchable Markdown Doc Site
To browse all developer docs (including this README, architecture blueprints, data models, and offline-sync protocols) in a beautiful locally-hosted website, run:
```bash
# 1. Activate the virtual environment
source .venv/bin/activate

# 2. Install documentation theme dependencies
pip install mkdocs-material

# 3. Serve the docs locally (hosts on port 8008 to prevent gateway collision)
make docs-serve
```
The unified docs website will then be available at: **[http://localhost:8008](http://localhost:8008)**.

---

## 📁 Project Structure

```
b2b-app/
├── services/
│   ├── orchestration/     # Go — API Gateway, Sync Broker
│   ├── identity/          # Python — Auth, Users, Tenants, Customers
│   ├── catalog/           # Python — Products, Categories, Pricing
│   ├── sales/             # Python — Orders, Invoices, Promotions
│   ├── route/             # Python — Beat Plans, VRP Route Optimization
│   ├── attendance/        # Python — Check-in/out, Leaves
│   └── notification/      # Python — WebSocket, FCM, VAPID, Email
├── infra/
│   ├── postgres/init.sql  # 6 database init + ENUM types
│   ├── pulsar/init-topics.sh  # Topic initialization
│   └── redis/redis.conf
├── docs/                  # All documentation (you are here)
├── docker-compose.yml     # Full stack
├── docker-compose.test.yml
├── Makefile
└── .env.example
```

---

## 📚 Documentation Index

| Document | Description |
|---|---|
| [developer-guide.md](./developer-guide.md) | Local dev setup, running tests, contributing |
| [auth-guide.md](./auth-guide.md) | JWT RS256 auth, RBAC roles, token lifecycle |
| [sync-protocol.md](./sync-protocol.md) | Offline sync push/pull, watermarks, idempotency |
| [data-models.md](./data-models.md) | Full database schemas for all 6 service DBs |
| [deployment.md](./deployment.md) | Docker Compose, key generation, K8s guidance |
| [api-reference/orchestration.md](./api-reference/orchestration.md) | Orchestration endpoints |
| [api-reference/identity.md](./api-reference/identity.md) | Identity service API |
| [api-reference/catalog.md](./api-reference/catalog.md) | Catalog service API |
| [api-reference/sales.md](./api-reference/sales.md) | Sales service API |
| [api-reference/route.md](./api-reference/route.md) | Route service API |
| [api-reference/attendance.md](./api-reference/attendance.md) | Attendance service API |
| [api-reference/notification.md](./api-reference/notification.md) | Notification service API |

---

## 🧪 Running Tests

```bash
# Run all tests (starts test infrastructure automatically)
make test-all

# Run a single service
make test-sales    # Includes rule engine tests
make test-identity
make test-catalog
make test-route
make test-attendance
make test-notification
make test-orchestration  # Go tests
```

---

## 🔑 Key Concepts

- **Offline-First**: Mobile clients work without connectivity and sync when online
- **Logical Watermarks**: Sync protocol uses integer sequence numbers, never device clocks
- **Server Re-validation**: Orders submitted offline are re-evaluated server-side for pricing compliance
- **CRDT Merging**: Inventory operations use delta operations, not absolute state
- **Server-Wins (Catalog)**: Price/SKU data cannot be mutated by offline clients
