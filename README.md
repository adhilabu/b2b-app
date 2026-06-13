# DSD B2B SaaS Platform

> **Mobile-first, offline-first SaaS platform for FMCG/CPG Direct Store Delivery (DSD) and Van Sales.**

This is a polyglot microservices platform: **Go** (Orchestration Gateway) and **Python/FastAPI** (Identity, Catalog, Sales, Route, Attendance, Notification).

For complete design details, sync protocols, database schemas, and deployment guidelines, view the comprehensive documentation in the [docs/](file:///Users/adhilabubacker/Projects/elixiretech/b2b-app/docs/README.md) directory.

---

## 🚀 Service Ports & API Documentation

Each service runs containerized and exposes its endpoints (including automated OpenAPI/Swagger interactive UI) directly:

| Service | Port | Base Proxy Path | Swagger UI Docs | OpenAPI Schema JSON |
|---|---|---|---|---|
| **Orchestration Gateway** | `8000` | `/` | — | — |
| **Identity Service** | `8001` | `/identity/*` | [Swagger Link](http://localhost:8001/docs) | [openapi.json](http://localhost:8001/openapi.json) |
| **Catalog Service** | `8002` | `/catalog/*` | [Swagger Link](http://localhost:8002/docs) | [openapi.json](http://localhost:8002/openapi.json) |
| **Sales Service** | `8003` | `/sales/*` | [Swagger Link](http://localhost:8003/docs) | [openapi.json](http://localhost:8003/openapi.json) |
| **Route Service** | `8004` | `/route/*` | [Swagger Link](http://localhost:8004/docs) | [openapi.json](http://localhost:8004/openapi.json) |
| **Attendance Service** | `8005` | `/attendance/*` | [Swagger Link](http://localhost:8005/docs) | [openapi.json](http://localhost:8005/openapi.json) |
| **Notification Service** | `8006` | `/notification/*` | [Swagger Link](http://localhost:8006/docs) | [openapi.json](http://localhost:8006/openapi.json) |

- **Gateway Root URL**: [http://localhost:8000/](http://localhost:8000/)
- **Gateway Health URL**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 📚 Viewing Developer Docs Locally (MkDocs)

We maintain searchable documentation for deployment, authentication guides, offline sync protocol rules, and database schemas. To host this locally on port `8008`:

```bash
# 1. Activate python virtual environment
source .venv/bin/activate

# 2. Install material docs theme
pip install mkdocs-material

# 3. Spin up the docs server
make docs-serve
```
The documentation website will be live at: **[http://localhost:8008](http://localhost:8008)**.

---

## 🛠️ Docker Quickstart

```bash
# 1. Create .env file and generate RSA/VAPID keys
make .env keygen

# 2. Start the database, messaging queues, and services stack
make up

# 3. Check health and statuses of the containers
make ps
```
