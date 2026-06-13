# Route Service API Reference

## Base URL
`http://localhost:8004` (direct) or `http://localhost:8000/route` (via Orchestration)

All endpoints require a valid JWT Bearer Token in the headers:
`Authorization: Bearer <access_token>`

---

## Beats & Routes

### Create Beat Plan
```
POST /beats/
```
Creates a new beat plan with a scheduled date and an ordered list of customer stops.

#### Request Body
```json
{
  "name": "Monday Core Beat — South Zone",
  "scheduled_date": "2026-06-15",
  "stops": [
    {
      "customer_id": "84814d48-356c-4860-93a0-717088b9bf9f",
      "customer_name": "Metro Food Mart",
      "latitude": 12.9715987,
      "longitude": 77.5945627,
      "estimated_visit_minutes": 20,
      "visit_notes": "Ask for manager John"
    },
    {
      "customer_id": "0d20dcfb-ef1e-450a-8a1a-c21ef806cebb",
      "customer_name": "Corner Store Plaza",
      "latitude": 12.9789,
      "longitude": 77.6432,
      "estimated_visit_minutes": 15,
      "visit_notes": "Collect pending payment"
    }
  ]
}
```

#### Response (201 Created)
```json
{
  "id": "e9fb443b-74b2-4d40-bf7d-c20a9a4697ff",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "name": "Monday Core Beat — South Zone",
  "scheduled_date": "2026-06-15",
  "is_optimized": false,
  "sync_version": 0,
  "created_at": "2026-06-13T18:55:21Z"
}
```

---

### List Beat Plans
```
GET /beats/
```
Retrieve all beat plans for the user's tenant, optionally filtered by date.

#### Query Parameters
- `scheduled_date` (optional): Filter beats scheduled for this specific date (format: `YYYY-MM-DD`).
- `skip` (optional): Pagination offset (default: `0`).
- `limit` (optional): Pagination limit (default: `50`, max: `200`).

#### Response (200 OK)
```json
[
  {
    "id": "e9fb443b-74b2-4d40-bf7d-c20a9a4697ff",
    "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
    "name": "Monday Core Beat — South Zone",
    "scheduled_date": "2026-06-15",
    "is_optimized": false,
    "sync_version": 0,
    "created_at": "2026-06-13T18:55:21Z"
  }
]
```

---

### Get Beat Plan Details (with Stops)
```
GET /beats/{beat_id}
```
Retrieves complete details of a beat plan including all mapped customer stops in sequence.

#### Response (200 OK)
```json
{
  "id": "e9fb443b-74b2-4d40-bf7d-c20a9a4697ff",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "name": "Monday Core Beat — South Zone",
  "scheduled_date": "2026-06-15",
  "is_optimized": false,
  "sync_version": 0,
  "created_at": "2026-06-13T18:55:21Z",
  "stops": [
    {
      "id": "18f4a13d-5573-455b-b9d9-bbdfab6f38a5",
      "customer_id": "84814d48-356c-4860-93a0-717088b9bf9f",
      "customer_name": "Metro Food Mart",
      "latitude": 12.9715987,
      "longitude": 77.5945627,
      "sequence": 0
    },
    {
      "id": "cbfa4c13-75b2-4a0b-8d19-4aefab82bb45",
      "customer_id": "0d20dcfb-ef1e-450a-8a1a-c21ef806cebb",
      "customer_name": "Corner Store Plaza",
      "latitude": 12.9789,
      "longitude": 77.6432,
      "sequence": 1
    }
  ]
}
```

---

### Optimize Route (VRP Solver)
```
POST /beats/{beat_id}/optimize
```
Executes the Google OR-Tools Vehicle Routing Problem (VRP) solver to find the mathematically optimal sequence for customer visits. Reorders `beat_stops` by distance in the database and updates `is_optimized` to `true`.

#### Response (200 OK)
```json
{
  "beat_id": "e9fb443b-74b2-4d40-bf7d-c20a9a4697ff",
  "is_optimized": true,
  "optimized_sequence": [
    "0d20dcfb-ef1e-450a-8a1a-c21ef806cebb",
    "84814d48-356c-4860-93a0-717088b9bf9f"
  ]
}
```

---

## Offline Sync

### Get Route Sync
```
GET /sync/
```
Returns all beat plans modified or created since the last sync version watermark.

#### Query Parameters
- `since_version` (optional): Last seen version watermark (default: `0`).

#### Response (200 OK)
```json
{
  "domain": "route",
  "watermark": 1,
  "beats": [
    {
      "id": "e9fb443b-74b2-4d40-bf7d-c20a9a4697ff",
      "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
      "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
      "name": "Monday Core Beat — South Zone",
      "scheduled_date": "2026-06-15",
      "is_optimized": true,
      "sync_version": 1,
      "created_at": "2026-06-13T18:55:21Z"
    }
  ]
}
```
