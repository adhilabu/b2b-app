.PHONY: help up down logs test test-all test-integration migrate keygen lint clean

SERVICES := orchestration identity catalog sales route attendance notification

PYTHON ?= python3
ifneq ($(findstring /,$(PYTHON)),)
  ABS_PYTHON := $(abspath $(PYTHON))
else
  ABS_PYTHON := $(shell which $(PYTHON) 2>/dev/null || echo $(PYTHON))
endif
PIP ?= $(ABS_PYTHON) -m pip

# ─────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────
help: ## Show this help message
	@echo ""
	@echo "  DSD B2B SaaS — Makefile Targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ─────────────────────────────────────────────
# ENVIRONMENT SETUP
# ─────────────────────────────────────────────
.env:
	@cp .env.example .env
	@echo "✅ .env created from .env.example — fill in your secrets!"

keygen: ## Generate RS256 JWT keypair and VAPID keys
	@mkdir -p infra/keys
	@echo "🔑 Generating RS256 JWT keypair..."
	@openssl genrsa -out infra/keys/private.pem 4096
	@openssl rsa -in infra/keys/private.pem -pubout -out infra/keys/public.pem
	@echo "✅ JWT keys generated: infra/keys/private.pem & public.pem"
	@echo ""
	@echo "🔑 Generating VAPID keys for Web Push..."
	@$(ABS_PYTHON) -c 'from py_vapid import Vapid; v=Vapid(); v.generate_keys(); v.save_key("infra/keys/vapid_private.pem"); v.save_public_key("infra/keys/vapid_public.pem")'
	@$(ABS_PYTHON) -c 'from cryptography.hazmat.primitives.serialization import load_pem_private_key; import base64; \
		key = load_pem_private_key(open("infra/keys/vapid_private.pem", "rb").read(), password=None); \
		priv = base64.urlsafe_b64encode(key.private_numbers().private_value.to_bytes(32, "big")).decode().rstrip("="); \
		pub = base64.urlsafe_b64encode(b"\x04" + key.public_key().public_numbers().x.to_bytes(32, "big") + key.public_key().public_numbers().y.to_bytes(32, "big")).decode().rstrip("="); \
		print("VAPID_PRIVATE_KEY=" + priv); print("VAPID_PUBLIC_KEY=" + pub)'
	@rm -f infra/keys/vapid_private.pem infra/keys/vapid_public.pem
	@echo ""
	@echo "⚠️  Add the VAPID keys to your .env file"

# ─────────────────────────────────────────────
# STACK MANAGEMENT
# ─────────────────────────────────────────────
up: .env ## Start the full stack (all services + infra)
	@docker compose up -d --build
	@echo "✅ Stack started. API Gateway: http://localhost:8000"

up-infra: ## Start infrastructure only (postgres, redis, pulsar)
	@docker compose up -d postgres redis pulsar pulsar-init
	@echo "✅ Infrastructure started"

down: ## Stop and remove containers
	@docker compose down

down-volumes: ## Stop and remove containers + volumes (⚠️ destroys data)
	@docker compose down -v

restart: ## Restart a specific service (usage: make restart SERVICE=identity)
	@docker compose restart $(SERVICE)

logs: ## Tail logs for all services (or specific: make logs SERVICE=identity)
ifdef SERVICE
	@docker compose logs -f $(SERVICE)
else
	@docker compose logs -f
endif

ps: ## Show running containers and their status
	@docker compose ps

# ─────────────────────────────────────────────
# DATABASE MIGRATIONS
# ─────────────────────────────────────────────
migrate: ## Run Alembic migrations for all Python services
	@for svc in identity catalog sales route attendance notification; do \
		echo "🗄️  Migrating $$svc..."; \
		docker compose run --rm $$svc alembic upgrade head; \
	done
	@echo "✅ All migrations applied"

migrate-service: ## Migrate a single service (usage: make migrate-service SERVICE=identity)
	@docker compose run --rm $(SERVICE) alembic upgrade head

migration-new: ## Create new migration (usage: make migration-new SERVICE=identity MSG="add column")
	@docker compose run --rm $(SERVICE) alembic revision --autogenerate -m "$(MSG)"

# ─────────────────────────────────────────────
# TESTING
# ─────────────────────────────────────────────
test-infra-up: ## Start test infrastructure
	@docker compose -f docker-compose.test.yml up -d --wait
	@echo "✅ Test infrastructure ready"

test-infra-down: ## Stop test infrastructure
	@docker compose -f docker-compose.test.yml down -v

test-identity: ## Run identity service tests
	@cd services/identity && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-catalog: ## Run catalog service tests
	@cd services/catalog && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-sales: ## Run sales service tests (includes rule engine tests)
	@cd services/sales && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-route: ## Run route service tests
	@cd services/route && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-attendance: ## Run attendance service tests
	@cd services/attendance && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-notification: ## Run notification service tests
	@cd services/notification && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing

test-orchestration: ## Run orchestration service tests (Go)
	@cd services/orchestration && go test ./... -v -coverprofile=coverage.out
	@cd services/orchestration && go tool cover -func=coverage.out

test-all: test-infra-up ## Run all service test suites
	@$(MAKE) test-orchestration
	@for svc in identity catalog sales route attendance notification; do \
		echo ""; \
		echo "🧪 Testing $$svc..."; \
		cd services/$$svc && $(ABS_PYTHON) -m pytest tests/ -v --cov=app --cov-report=term-missing -q; \
		cd ../..; \
	done
	@$(MAKE) test-infra-down
	@echo ""
	@echo "✅ All tests complete"

test-integration: test-infra-up ## Run integration tests against live test infra
	@$(ABS_PYTHON) -m pytest tests/integration/ -v --tb=short
	@$(MAKE) test-infra-down

# ─────────────────────────────────────────────
# LINTING
# ─────────────────────────────────────────────
lint: ## Lint all services
	@echo "🔍 Linting Python services..."
	@for svc in identity catalog sales route attendance notification; do \
		echo "  → $$svc"; \
		cd services/$$svc && ruff check app/ tests/ && cd ../..; \
	done
	@echo "🔍 Linting Go service..."
	@cd services/orchestration && golangci-lint run ./...
	@echo "✅ Lint complete"

format: ## Format all Python services with black
	@for svc in identity catalog sales route attendance notification; do \
		cd services/$$svc && black app/ tests/ && cd ../..; \
	done

# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────
shell: ## Open a shell in a running service (usage: make shell SERVICE=identity)
	@docker compose exec $(SERVICE) /bin/bash

clean: ## Remove all build artifacts and caches
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Cleaned"

build: ## Build all Docker images without starting
	@docker compose build

build-service: ## Build a single service image (usage: make build-service SERVICE=identity)
	@docker compose build $(SERVICE)

build-go: ## Build Go orchestration binary locally (requires Go toolchain)
	@cd services/orchestration && go build -o bin/orchestration ./cmd/...
	@echo "✅ Orchestration binary: services/orchestration/bin/orchestration"

vet-go: ## Run go vet on orchestration service
	@cd services/orchestration && go vet ./...

# ─────────────────────────────────────────────
# VAN SALES (PHASE 2)
# ─────────────────────────────────────────────
van-checkout: ## Start of day: load a van (usage: make van-checkout - see API docs for payload)
	@echo "ℹ️  Use POST /sales/van/stocks/ with the van stock payload."
	@echo "   See docs/api-reference/van-sales.md for request format."

van-settle: ## End of day: settle a van (usage: make van-settle - see API docs for payload)
	@echo "ℹ️  Use POST /sales/van/settle/ with the settlement payload."
	@echo "   Note: Requires the driver to be checked in via attendance service."

# ─────────────────────────────────────────────
# DOCS
# ─────────────────────────────────────────────
docs-serve: ## Serve docs locally (requires mkdocs: pip install mkdocs-material)
	@mkdocs serve --config-file docs/mkdocs.yml -a localhost:8008
