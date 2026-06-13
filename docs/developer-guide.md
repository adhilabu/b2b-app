# Developer Guide

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker + Compose | v24+ | [docker.com](https://docker.com) |
| Python | 3.12+ | `brew install python` |
| Go | 1.22+ | `brew install go` |
| OpenSSL | 3.x | Pre-installed on macOS |
| Make | 3.8+ | Pre-installed |

---

## Initial Setup

### 1. Clone and configure

```bash
cd /path/to/b2b-app

# Copy env template
cp .env.example .env
```

### 2. Generate cryptographic keys

```bash
make keygen
```

This will:
- Generate RSA-4096 keypair → `infra/keys/private.pem` and `infra/keys/public.pem`
- Print VAPID keys → copy these into `.env`

> ⚠️ Add `infra/keys/` to `.gitignore` immediately:
> ```bash
> echo "infra/keys/" >> .gitignore
> ```

### 3. Start infrastructure only (faster for dev)

```bash
# Start just postgres, redis, pulsar
make up-infra

# Or start everything
make up
```

### 4. Run a specific service locally (for faster iteration)

```bash
cd services/identity
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

---

## Running Tests

### All services
```bash
make test-all
```

### Single service
```bash
make test-sales        # Includes rule engine + tax calculator tests
make test-catalog      # Includes sync watermark tests
make test-orchestration # Go JWT middleware tests
```

### With coverage report
```bash
cd services/sales
python -m pytest tests/ -v --cov=app --cov-report=html
open htmlcov/index.html
```

---

## Local Development Tips

### Hot reload for Python services
```bash
cd services/catalog
uvicorn app.main:app --reload --port 8002
```

### Watching logs
```bash
make logs                    # All services
make logs SERVICE=sales      # Just sales
```

### Connecting to the database directly
```bash
docker compose exec postgres psql -U b2b_admin -d sales_db
```

### Inspecting Redis
```bash
docker compose exec redis redis-cli
> KEYS refresh:*             # List refresh tokens
> TTL refresh:{jti}          # Check TTL
```

### Pulsar admin UI
Open: [http://localhost:8080](http://localhost:8080)

---

## Service Development Pattern

All Python services follow this structure:

```
services/{name}/
├── app/
│   ├── main.py          # FastAPI app + lifespan + all routes
│   ├── config.py        # Settings (pydantic-settings)
│   ├── database.py      # Async SQLAlchemy engine
│   ├── models/          # SQLAlchemy ORM models
│   ├── schemas/         # Pydantic request/response schemas
│   ├── routers/         # FastAPI routers
│   └── auth/            # JWT verify dependency
├── tests/
│   ├── conftest.py      # Shared fixtures
│   └── test_*.py
├── Dockerfile
├── requirements.txt
└── pytest.ini
```

### Adding a new endpoint

1. Add the SQLAlchemy model in `app/models/`
2. Add Pydantic schemas in `app/schemas/`
3. Add the router in `app/routers/`
4. Register the router in `app/main.py`
5. Write tests in `tests/test_*.py`
6. Run `make test-{service}`

### Adding a new Pulsar event

1. Add the topic to `infra/pulsar/init-topics.sh`
2. Publish from the producer service using `app/events/publisher.py`
3. Consume in the target service by adding a consumer

---

## Environment Variables Reference

See [.env.example](../.env.example) for the full list.

### Critical variables

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | Shared DB password |
| `JWT_PRIVATE_KEY_PATH` | ✅ (Identity only) | Path to RSA private key |
| `JWT_PUBLIC_KEY_PATH` | ✅ (all services) | Path to RSA public key |
| `VAPID_PRIVATE_KEY` | For Web Push | VAPID private key |
| `VAPID_PUBLIC_KEY` | For Web Push | VAPID public key |
| `FIREBASE_CREDENTIALS_PATH` | For FCM | Path to Firebase service account JSON |

---

## Linting and Formatting

```bash
# Lint all Python services
make lint

# Format with black
make format

# Go lint (requires golangci-lint)
cd services/orchestration && golangci-lint run ./...
```

---

## Troubleshooting

### Pulsar health check fails on startup
Pulsar takes ~60 seconds to start. The `pulsar-init` container waits for it automatically. If services timeout waiting for Pulsar, increase `start_period` in `docker-compose.yml`.

### JWT key not found
Ensure you ran `make keygen` and the `infra/keys/` directory exists.

### Database migration errors
```bash
# Re-run init SQL manually
docker compose exec postgres psql -U b2b_admin -f /docker-entrypoint-initdb.d/init.sql
```

### Port conflicts
Default ports: 8000-8006 (services), 5432 (postgres), 6379 (redis), 6650/8080 (pulsar).
Change in `docker-compose.yml` if conflicts exist.
