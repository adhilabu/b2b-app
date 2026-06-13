# Catalog, Route, and Attendance API Reference

## Catalog Service — http://localhost:8002

### Categories
| Method | Path | Description |
|---|---|---|
| `POST` | `/categories/` | Create category |
| `GET` | `/categories/?parent_id=uuid` | List categories (supports hierarchical filter) |

### Products
| Method | Path | Description |
|---|---|---|
| `POST` | `/products/` | Create product SKU |
| `GET` | `/products/` | List products (filter by category, status) |
| `GET` | `/products/{id}` | Get product |
| `PATCH` | `/products/{id}` | Update product (increments sync_version) |
| `POST` | `/products/{id}/prices/` | Set pricing (server-wins) |
| `GET` | `/products/{id}/prices/` | Get active prices |
| `GET` | `/sync/?since_version=N` | Delta sync for mobile |

> **Server-Wins**: Only server-side mutations to pricing are allowed. Offline clients cannot push catalog changes.

---

## Route Service — http://localhost:8004

### Beat Plans
| Method | Path | Description |
|---|---|---|
| `POST` | `/beats/` | Create beat with stops |
| `GET` | `/beats/?scheduled_date=2024-06-13` | List beats |
| `GET` | `/beats/{id}` | Get beat with all stops |
| `POST` | `/beats/{id}/optimize` | Run VRP optimizer (OR-Tools) |
| `GET` | `/sync/?since_version=N` | Delta sync for mobile |

**Optimize response:**
```json
{
  "beat_id": "uuid",
  "is_optimized": true,
  "optimized_sequence": ["customer-uuid-3", "customer-uuid-1", "customer-uuid-2"]
}
```

The stops are reordered in the database by the optimal Haversine/VRP sequence.

---

## Attendance Service — http://localhost:8005

### Attendance
| Method | Path | Description |
|---|---|---|
| `POST` | `/attendance/check-in` | Record GPS check-in |
| `POST` | `/attendance/check-out` | Record GPS check-out |
| `GET` | `/attendance/` | List attendance records |
| `GET` | `/attendance/availability/{user_id}` | **Operational blocking check** |

**Availability check** (used by Orchestration before allowing route start / settlement):
```json
GET /attendance/availability/user-uuid?check_date=2024-06-13

{
  "user_id": "uuid",
  "check_date": "2024-06-13",
  "is_available": false,
  "status": "on_leave",
  "reason": "Approved casual leave"
}
```

### Leaves
| Method | Path | Description |
|---|---|---|
| `POST` | `/leaves/` | Submit leave request |
| `GET` | `/leaves/` | List leaves (filter by user, status) |
| `PATCH` | `/leaves/{id}/approve` | Approve leave (manager+) |
| `PATCH` | `/leaves/{id}/reject` | Reject leave (manager+) |
