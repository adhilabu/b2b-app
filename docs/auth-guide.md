# Auth Guide — JWT RS256 & RBAC

## Overview

The platform uses **RS256 asymmetric JWT** (JSON Web Tokens) for authentication and authorization:

- The **Identity Service** holds the RSA **private key** and issues tokens
- All other services hold only the RSA **public key** and verify tokens
- Tokens are **never issued by any service other than Identity**

---

## JWT Token Structure

### Access Token (15 minutes TTL)

```json
{
  "sub": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "roles": ["sales_rep"],
  "exp": 1718000000,
  "iat": 1717999100,
  "jti": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| Claim | Type | Description |
|---|---|---|
| `sub` | UUID string | User ID |
| `tenant_id` | UUID string | Tenant the user belongs to |
| `roles` | string[] | User's roles |
| `exp` | Unix timestamp | Token expiry |
| `iat` | Unix timestamp | Token issued at |
| `jti` | UUID string | Unique token ID (for revocation) |

### Refresh Token (7 days TTL)

The refresh token is an **opaque UUID** (the `jti`) stored as a Redis key:
```
refresh:{jti} → user_id   (TTL: 7 days)
```

When a refresh is requested:
1. Old refresh token is **deleted** from Redis (rotation)
2. New access token + new refresh token are issued

This prevents refresh token replay attacks.

---

## Token Flow

```
Client                Identity Service            Redis
  │                         │                      │
  ├──POST /auth/login───────►│                      │
  │                         ├──verify password      │
  │                         ├──create access_token  │
  │                         ├──create refresh_jti──►│ SETEX refresh:{jti} 7d
  │◄──{access_token,────────┤                      │
  │    refresh_token}       │                      │
  │                         │                      │
  ├──GET /catalog/*─────────►│(Orchestration)       │
  │  Authorization: Bearer  │                      │
  │                         ├──verify RS256 sig     │
  │                         ├──forward to catalog   │
  │◄──200 OK────────────────┤                      │
  │                         │                      │
  ├──POST /auth/refresh─────►│                      │
  │  {refresh_token}        ├──GET refresh:{jti}──►│
  │                         │◄──user_id────────────┤
  │                         ├──DELETE refresh:{jti}►│
  │                         ├──issue new tokens    │
  │◄──{new_access_token}────┤                      │
```

---

## RBAC — Roles Reference

| Role | Description | Permissions |
|---|---|---|
| `admin` | System administrator | Full access to all endpoints |
| `manager` | Branch/region manager | User management, reports, approve leaves |
| `supervisor` | Field supervisor | View team, approve leaves |
| `sales_rep` | Field sales representative | Own orders, customers, attendance |
| `driver` | Van sales driver | Van inventory, spot billing |

### Role Hierarchy (for permissions)

```
admin
  └── manager
        └── supervisor
              └── sales_rep / driver
```

### Endpoint Authorization Matrix

| Endpoint | admin | manager | supervisor | sales_rep |
|---|:---:|:---:|:---:|:---:|
| Create User | ✅ | ❌ | ❌ | ❌ |
| List Users | ✅ | ✅ | ✅ | ❌ |
| Create Customer | ✅ | ✅ | ✅ | ✅ |
| Create Order | ✅ | ✅ | ✅ | ✅ |
| Confirm Order | ✅ | ✅ | ❌ | ❌ |
| Create Promotion | ✅ | ✅ | ❌ | ❌ |
| Approve Leave | ✅ | ✅ | ❌ | ❌ |
| Create Beat Plan | ✅ | ✅ | ✅ | ❌ |
| Create Tenant | ✅ | ❌ | ❌ | ❌ |

---

## How Downstream Services Verify Tokens

Each downstream service (catalog, sales, route, attendance, notification) uses the **public key only**:

```python
# services/catalog/app/auth/dependencies.py
from jose import jwt

payload = jwt.decode(
    token,
    settings.jwt_public_key,   # RSA public key
    algorithms=["RS256"],
)
tenant_id = payload["tenant_id"]
roles = payload["roles"]
```

The `tenant_id` extracted from the JWT is used to scope all database queries:
```python
# All queries are automatically scoped to the user's tenant
query = select(Product).where(Product.tenant_id == current_user.tenant_id)
```

---

## Security: Token Revocation

Access tokens are **short-lived (15 min)** and cannot be individually revoked.
Refresh tokens **can be revoked** by deleting the Redis key:

```bash
# Revoke all sessions for a user (admin action):
redis-cli DEL "refresh:{jti}"

# Or via API:
POST /auth/logout
{"refresh_token": "{jti}"}
```

---

## Generating Keys (Development)

```bash
# Generate RSA-4096 keypair
make keygen

# Or manually:
openssl genrsa -out infra/keys/private.pem 4096
openssl rsa -in infra/keys/private.pem -pubout -out infra/keys/public.pem
```

> **⚠️ NEVER commit `private.pem` to version control.**
> Add `infra/keys/` to `.gitignore`.

---

## Web Push VAPID Keys

The Notification service uses VAPID keys for browser push:

```bash
# Generate VAPID keys:
pip install pywebpush
python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('VAPID_PRIVATE_KEY=' + v.private_key)
print('VAPID_PUBLIC_KEY=' + v.public_key)
"
```

Add the output to your `.env` file.
The public key is served at: `GET /notification/subscriptions/vapid-public-key`
