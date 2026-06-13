# Orchestration Service API Reference

## Base URL
`http://localhost:8000`

The Orchestration service is the **single entry point** for all clients. It:
- Validates JWT tokens (RS256)
- Routes requests to downstream services
- Handles the offline sync protocol

---

## Health Check (Public)
```
GET /health
```

---

## Authentication (Public — no JWT required)

### Login
```
POST /auth/login
Content-Type: application/json

{"email": "user@example.com", "password": "your-password"}
```
**Response:**
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiJ9...",
  "refresh_token": "550e8400-e29b-41d4-a716-446655440000",
  "token_type": "bearer",
  "expires_in": 900
}
```

### Refresh Token
```
POST /auth/refresh
{"refresh_token": "jti-uuid"}
```

### Logout
```
POST /auth/logout
{"refresh_token": "jti-uuid"}
```

---

## Sync Protocol (JWT required)

### Push (Mobile → Server)
```
POST /sync/push
Authorization: Bearer <token>
```

### Pull (Server → Mobile)
```
GET /sync/pull?wm_sales=42&wm_catalog=10&wm_identity=18&wm_route=7&wm_attendance=5
Authorization: Bearer <token>
```

See [sync-protocol.md](../sync-protocol.md) for full details.

---

## Proxied Service Routes (JWT required)

All protected routes are proxied to downstream services. The JWT is validated at the gateway and forwarded.

| Route Pattern | Proxied To |
|---|---|
| `/identity/*` | Identity Service :8001 |
| `/catalog/*` | Catalog Service :8002 |
| `/sales/*` | Sales Service :8003 |
| `/route/*` | Route Service :8004 |
| `/attendance/*` | Attendance Service :8005 |
| `/notification/*` | Notification Service :8006 |

**Example:**
```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"pass"}' | jq -r .access_token)

# List products via Orchestration → Catalog
curl http://localhost:8000/catalog/products/ \
  -H "Authorization: Bearer $TOKEN"
```
