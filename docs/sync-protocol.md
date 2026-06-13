# Distributed Offline Sync Protocol

## Overview

The sync protocol enables mobile apps to work **completely offline** and synchronize state with the server when connectivity is restored.

Key design principles:
- **No device clock dependency** — Uses logical sequence numbers (watermarks), not timestamps
- **Idempotent** — Safe to retry; duplicate events are deduplicated server-side
- **Conflict-free on critical data** — Server always wins for pricing; CRDT merging for inventory
- **Parallel aggregation** — Pull syncs all domains simultaneously

---

## Architecture

```
Mobile App                    Orchestration              Domain Services
    │                              │                          │
    │─── PUSH (events) ───────────►│                          │
    │  {events: [...]}             ├──route event─────────────►│ Sales
    │                              ├──route event─────────────►│ Identity
    │                              ├──route event─────────────►│ Attendance
    │◄── {accepted, failed} ───────┤                          │
    │                              │                          │
    │─── PULL (watermarks) ───────►│                          │
    │  ?wm_sales=42&wm_catalog=10  ├──GET /sync/?since=42─────►│ Sales
    │                              ├──GET /sync/?since=10─────►│ Catalog
    │                              ├──GET /sync/?since=0──────►│ Route
    │                              │◄─ delta ─────────────────┤
    │◄── {deltas, watermarks} ─────┤                          │
```

---

## Push Sync (Mobile → Server)

### Request

```
POST /sync/push
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "device_id": "device-uuid-1234",
  "push_timestamp": 1718000000,
  "events": [
    {
      "event_id": "evt-001",
      "event_type": "OrderCreated",
      "domain": "sales",
      "timestamp": 1717999000,
      "payload": {
        "client_uuid": "offline-order-uuid",
        "customer_id": "customer-uuid",
        "items": [...]
      }
    },
    {
      "event_id": "evt-002",
      "event_type": "AttendanceLogged",
      "domain": "attendance",
      "timestamp": 1717998000,
      "payload": {
        "check_in_at": "2024-06-13T09:00:00Z",
        "latitude": 12.97,
        "longitude": 77.59
      }
    }
  ],
  "last_watermarks": {
    "sales": 42,
    "identity": 18,
    "catalog": 10,
    "route": 7,
    "attendance": 5
  }
}
```

### Event Type → Domain Routing

| Event Type | Domain Service |
|---|---|
| `OrderCreated`, `OrderUpdated` | Sales |
| `InvoiceCreated` | Sales |
| `SalesReturnCreated` | Sales |
| `CustomerCreated`, `CustomerUpdated` | Identity |
| `AttendanceLogged` | Attendance |
| `LeaveRequested` | Attendance |
| `BeatPlanCreated` | Route |

### Response

```json
{
  "accepted": 2,
  "failed": [],
  "message": "Processed 2 events"
}
```

---

## Pull Sync (Server → Mobile)

### Request

```
GET /sync/pull?wm_sales=42&wm_catalog=10&wm_identity=18&wm_route=7&wm_attendance=5
Authorization: Bearer <access_token>
```

### Response

```json
{
  "fetched_at": "2024-06-13T10:00:00Z",
  "watermarks": {
    "sales": 55,
    "catalog": 12,
    "identity": 20,
    "route": 9,
    "attendance": 8
  },
  "deltas": {
    "sales": {
      "domain": "sales",
      "watermark": 55,
      "orders": [...]
    },
    "catalog": {
      "domain": "catalog",
      "watermark": 12,
      "products": [...],
      "categories": [...]
    },
    "identity": {
      "domain": "identity",
      "watermark": 20,
      "entities": [...]
    }
  }
}
```

---

## Idempotency — client_uuid

Every entity created offline uses a **client-generated UUID** (`client_uuid`).

All services implement **ON CONFLICT DO UPDATE** semantics:

```python
# Idempotent create — safe to retry
if data.client_uuid:
    existing = await db.execute(
        select(Order).where(Order.client_uuid == data.client_uuid)
    )
    if existing.scalar_one_or_none():
        return existing.scalar_one_or_none()  # Return existing, don't create duplicate
```

This means:
- Mobile app can retry failed sync pushes safely
- Network timeouts don't cause duplicate records
- The mobile app can use the same `client_uuid` from its local SQLite record

---

## Conflict Resolution Strategies

| Domain | Strategy | Details |
|---|---|---|
| **User & Customer Metadata** | Last-Write-Wins (LWW) | Based on server reception timestamp |
| **Catalog (Pricing, SKUs)** | Server-Wins | Offline clients cannot mutate; server values always override |
| **Sales (Inventory Ops)** | CRDT Delta | Client pushes `"Decrement by 5"` not `"Set to 95"` |
| **Sales (Orders)** | Server Re-validation | Rule engine recalculates totals; flags mismatches as exceptions |

### Server Re-validation Example

```json
// Mobile submitted order with client_total: 1180
// Server calculates: 1200 (expired promo cache)
// Difference: 1.67% > 1% tolerance

// Order status becomes: "exception_review_required"
// exception_reason: "Client total 1180.00 differs from server total 1200.00 by 1.67%"
```

---

## Sync Watermark Sequence

Each service maintains a monotonically-increasing `sync_version` integer per entity.

On every update:
```python
product.sync_version += 1
```

During pull sync, services return only entities with `sync_version > since_version`:
```python
select(Product).where(
    Product.tenant_id == tenant_id,
    Product.sync_version > since_version,
)
```

The client stores the returned `watermark` and uses it as `since_version` on the next pull.

---

## Sync Frequency Recommendations

| Scenario | Push | Pull |
|---|---|---|
| App foreground, connected | Immediate (on action) | Every 5 min |
| App background, connected | On reconnect | Every 30 min |
| App offline | Store in outbox | On reconnect |
| End of day | Force sync before settlement | — |
