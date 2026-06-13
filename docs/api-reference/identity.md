# Identity Service API Reference

## Base URL
`http://localhost:8001` (direct) or via `http://localhost:8000/identity/*` (Orchestration)

---

## Authentication

### POST /auth/login
```json
{"email": "admin@example.com", "password": "SecurePass123"}
```
Returns `access_token` (RS256 JWT, 15 min) + `refresh_token` (opaque UUID, 7 days).

### POST /auth/refresh
```json
{"refresh_token": "jti-uuid"}
```

### POST /auth/logout
```json
{"refresh_token": "jti-uuid"}
```
Revokes the refresh token from Redis.

### GET /auth/me
Returns the currently authenticated user's profile.

---

## Users

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/users/` | admin | Create user |
| `GET` | `/users/` | manager+ | List users in tenant |
| `GET` | `/users/{id}` | any | Get user by ID |
| `PATCH` | `/users/{id}` | manager+ | Update user |
| `DELETE` | `/users/{id}` | admin | Deactivate user |

---

## Customers

| Method | Path | Description |
|---|---|---|
| `POST` | `/customers/` | Create customer (supports `client_uuid` for offline) |
| `GET` | `/customers/` | List customers (filter by city, is_active) |
| `GET` | `/customers/sync?since_version=N` | Get delta for offline sync |
| `GET` | `/customers/{id}` | Get customer |
| `PATCH` | `/customers/{id}` | Update customer |

---

## Tenants

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/tenants/` | admin | Create tenant |
| `GET` | `/tenants/me` | admin | Get current tenant |
| `PATCH` | `/tenants/{id}` | admin | Update settings |
