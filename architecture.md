
Executive Summary
We propose a mobile-first, offline-first SaaS architecture for FMCG/CPG DSD (Direct Store Delivery) and Van Sales, structured around a highly scalable Microservices Architecture. The system will support Phase 1 features (Customer management, Orders, Promotions, Route planning, and offline sync) with a Phase 2 extension for Van Sales (spot billing, cash reconciliation, and settlement).
To ensure operational independence and fault tolerance, the backend is decomposed into seven core microservice domains: Orchestration, User & Tenant Identity, Catalog, Sales & Promotions, Routing, Attendance, and Notifications. Mobile clients maintain local stores (e.g., SQLite) for offline use and sync changes via the Orchestration gateway using an event-sourced, logical-watermark protocol that eliminates reliance on device clocks.
This polyglot architecture allows for targeted technology choices: high-throughput entry points like the Orchestration service can be implemented in Go, while domain-heavy services like Sales or Routing leverage Python (FastAPI) and TypeScript (Node.js). Redis is utilized for short-lived data, distributed caching, and event streams. PostgreSQL (v14+) is used with a strict database-per-service pattern.

flowchart TD
    subgraph MobileClients
      MA["Sales Rep Mobile App
(Local DB, Offline Cache)"]
    end
    
    subgraph API_Gateway
      ORCH["Orchestration Service
(Sync Broker & API Gateway)"]
    end

    subgraph Microservices
      ID["User, Auth, Customer, Tenant & Settings"]
      CAT["Products & Category"]
      SALES["Order, Invoice, Sales Return, Promotion"]
      ROUTE["Beat Plan & Route Plan"]
      HR["Attendance & Leave Management"]
      NOTIFY["Notification Service"]
    end

    subgraph Infrastructure
      DB[(PostgreSQL Clusters)]
      Cache[(Redis Event Bus & Cache)]
    end

    MA -->|"HTTPS/gRPC"| ORCH
    ORCH --> ID
    ORCH --> CAT
    ORCH --> SALES
    ORCH --> ROUTE
    ORCH --> HR
    ORCH --> NOTIFY
    
    SALES --> Cache
    ROUTE --> Cache
    ID --> DB
    SALES --> DB


Figure: High-level microservices architecture.

System Architecture: The 7 Core Microservices
The monolithic application state has been distributed into bounded contexts. Each service exposes RESTful or gRPC APIs and communicates asynchronously via Apache Pulsar or Kafka for cross-domain events.
1. Orchestration Service (API Gateway & Sync Broker)
Role: The unified entry point for all mobile and web clients. It acts as a reverse proxy, aggregates data for complex views, and manages the distributed offline sync protocol.
Sync Handling: Receives the batched event payload (Push) from mobile apps, decodes it, and routes the individual domain events (e.g., OrderCreated, CustomerUpdated) to the respective microservices. It also aggregates the Logical Watermark (Pull) responses from all services to send a unified delta back to the client.
2. User, Auth, Customer, Tenant & Setting Service
Role: The identity and access management backbone.
Responsibilities: Issues OAuth2/JWT tokens, enforces Role-Based Access Control (RBAC), manages global tenant configurations, and serves as the source of truth for Customer (outlet) metadata.
Feature Focus: When a field rep creates a new customer outlet offline, this service handles the sync upsert and validates the captured GPS coordinates against territory boundaries (geo-fencing).
3. Products and Category Service (Catalog)
Role: Product Information Management (PIM).
Responsibilities: Manages Product SKUs, hierarchical categories, and base pricing. This data is highly cacheable.
Conflict Resolution: Operates on a Server-Wins model. Mobile clients are prohibited from mutating base pricing catalogs offline.
4. Order, Invoice, Sales Return, Promotion Service
Role: The transactional engine of the platform.
Responsibilities: Manages the complete order lifecycle, inventory earmarking, spot billing, and returns processing.
Rule Engine Integration: Contains the AST JSON Rule Engine for evaluating complex trade promotions (volume slabs, BOGO, Free-of-Cost item injections) and calculating trade taxes.
Server Re-validation: Re-evaluates offline-submitted orders. If the client-calculated total differs from the server-calculated total (due to expired local caches), the order is flagged as Exception_Review_Required.
5. Beat Plan, Route Plan Service
Role: Logistics, scheduling, and AI optimization.
Responsibilities: Groups customers into Beats (territories) and computes daily optimized Routes using Vehicle Routing Problem (VRP) solvers like Google OR-Tools.
Integration: Subscribes to OrderConfirmed events from the Sales service to factor pending deliveries into the daily route computation.
6. Attendance and Leave Management Service
Role: Workforce operations and availability tracking.
Responsibilities: Manages field rep check-ins, check-outs, leave requests, and shift adherence.
Operational Blocking: If a user attempts to initiate a Route or perform End-of-Day Settlement but is marked "On Leave" or "Absent" in this service, the Orchestrator blocks the action.
7. Notification Service
Role: Centralized messaging and alerts.
Responsibilities: Listens to domain events via Apache Pulsar (e.g., RouteOptimized, OrderRejected) and pushes updates to mobile clients via WebSockets, FCM (Firebase), or email.

Distributed Offline Sync Protocol
Adapting the sync protocol to a microservices architecture requires the Orchestration Service to act as the synchronization choreographer.
Event Sourcing (Push): The offline client sends an ordered array of events from its local outbox. The Orchestrator unwraps this payload and publishes the events to Apache Pulsar. The relevant microservices consume these streams independently. For example, a SyncPayload might contain both an AttendanceLogged event and an OrderCreated event; the HR service consumes the former, the Sales service consumes the latter.
Logical Watermark (Pull): Each microservice maintains its own append-only sync_journal and watermark sequence. During a pull request, the Orchestrator queries all downstream services in parallel with the client's last known watermarks for each domain, aggregates the deltas, and returns a composite JSON to the mobile app.
Idempotent Upserts: Every entity created offline relies on a client_uuid. All microservices implement INSERT ... ON CONFLICT DO UPDATE to ensure distributed retries do not result in duplicate records.

Entity Domain (Service)
Conflict Resolution Strategy
User & Customer Metadata
Last-Write-Wins (LWW) based on reception timestamp.
Sales (Inventory Operations)
Additive CRDT Merging: Clients push delta operations ("Decrement by 5") rather than absolute state.
Catalog (Pricing, SKUs)
Server-Wins: Strict enforcement; offline clients cannot mutate.
Sales (Orders)
Server Re-validation: Rule engine recalculates totals to prevent pricing non-compliance.


Data Model & Database-per-Service Strategy
To avoid a monolithic database bottleneck, we adopt a Database-per-Service pattern. Each service manages its own data persistence, while continuing to utilize schema-based multi-tenancy internally.
Sales & Promotion Service Schema Example
The Promotions module uses PostgreSQL ENUM types for validation and JSONB for flexible parameter payloads.
-- Executed within the Sales Service Database, partitioned by Tenant Schema

CREATE TYPE condition_type AS ENUM ('min_order_amount', 'has_sku', 'has_any_sku');
CREATE TYPE action_type AS ENUM ('percentage_off_order', 'free_item', 'bogo');

CREATE TABLE promotion (
  id                 UUID PRIMARY KEY,
  business_id        UUID NOT NULL, -- Logical reference to Tenant Service
  code               TEXT UNIQUE,
  is_stackable       BOOLEAN NOT NULL DEFAULT false,
  valid_from         TIMESTAMPTZ,
  valid_to           TIMESTAMPTZ,
  is_active          BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE promotion_condition (
  id             UUID PRIMARY KEY,
  promotion_id   UUID NOT NULL REFERENCES promotion(id) ON DELETE CASCADE,
  condition_type condition_type NOT NULL,
  parameters     JSONB NOT NULL DEFAULT '{}' 
  -- e.g., {"min_amount": 500}
);

CREATE TABLE promotion_action (
  id           UUID PRIMARY KEY,
  promotion_id UUID NOT NULL REFERENCES promotion(id) ON DELETE CASCADE,
  action_type  action_type NOT NULL,
  parameters   JSONB NOT NULL DEFAULT '{}'
);


Beat & Route Service Schema Example
-- Executed within the Route Service Database

CREATE TABLE beats (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL, -- Logical reference to Auth Service
  scheduled_date DATE NOT NULL,
  sync_version   BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE beat_stops (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  beat_id     UUID REFERENCES beats(id),
  customer_id UUID NOT NULL, -- Logical reference to Customer Service
  sequence    INT NOT NULL   -- Optimized sequence via OR-Tools
);



Phase 2: Van Sales & Inter-Service Operations
In Phase 2, delivery vehicles operate as virtual mobile warehouses requiring cross-service coordination.
Start-of-Day Checkout:
The HR Service confirms the driver is clocked in.
The Route Service assigns the daily Beat plan.
The Sales Service commits a formal stock transfer from the Depot to the Van's virtual warehouse ID and pre-allocates blocks of sequential offline invoice numbers (via Redis).
End-of-Day Settlement Cockpit:
Triggered via the Orchestration Service, this launches an async saga.
Sales Service: Reconciles expected inventory (Morning Load - Goods Sold) vs. actual physical return. Reconciles offline payments against total invoices, utilizing Tolerance Groups for rounding errors.
HR Service: Automatically logs the end-of-shift timestamp upon successful settlement clearance.

Security and Inter-Service Access
Authentication & JWT: The Identity service issues a JWT containing the user's roles and tenant_id. The Orchestration gateway validates the signature and forwards the JWT downstream. Each microservice sets its database search_path dynamically based on the tenant_id claim.
Service-to-Service Security: Internal microservice communication is secured via mTLS (Mutual TLS) within the Kubernetes cluster, ensuring that only the Orchestration layer or authorized peer services can invoke internal APIs.
