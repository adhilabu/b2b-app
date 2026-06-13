# Sales Service API Reference

## Base URL
`http://localhost:8003` (direct) or `http://localhost:8000/sales` (via Orchestration)

All endpoints require: `Authorization: Bearer <access_token>`

---

## Orders

### Create Order
```
POST /orders/
```

**Request Body:**
```json
{
  "client_uuid": "offline-generated-uuid",
  "customer_id": "uuid",
  "payment_method": "cash",
  "items": [
    {
      "sku": "JUICE-001",
      "product_id": "uuid",
      "name": "Mango Juice 200ml",
      "quantity": 24,
      "unit_price": 18.50,
      "tax_rate_percent": 12.0
    }
  ],
  "promotion_codes": ["BULK10"],
  "client_total": 496.32,
  "is_intra_state": true,
  "notes": "Deliver before noon"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "client_uuid": "offline-generated-uuid",
  "tenant_id": "uuid",
  "customer_id": "uuid",
  "status": "submitted",
  "subtotal": 444.00,
  "discount_amount": 44.40,
  "tax_amount": 47.95,
  "grand_total": 447.55,
  "exception_reason": null,
  "sync_version": 0,
  "created_at": "2024-06-13T10:00:00Z"
}
```

> **Server Re-validation**: If `client_total` differs from `grand_total` by >1%, `status` is set to `exception_review_required` and `exception_reason` explains the discrepancy.

---

### List Orders
```
GET /orders/?status=submitted&customer_id=uuid&skip=0&limit=50
```

### Get Order
```
GET /orders/{order_id}
```

### Confirm Order
```
PATCH /orders/{order_id}/confirm
```
Requires: `manager` or `admin` role.

---

## Promotions

### Create Promotion
```
POST /promotions/
```

**Example: 10% off orders ≥ ₹500**
```json
{
  "code": "BULK10",
  "is_stackable": false,
  "valid_from": "2024-06-01T00:00:00Z",
  "valid_to": "2024-06-30T23:59:59Z",
  "conditions": [
    {
      "condition_type": "min_order_amount",
      "parameters": {"min_amount": 500}
    }
  ],
  "actions": [
    {
      "action_type": "percentage_off_order",
      "parameters": {"percentage": 10}
    }
  ]
}
```

**Example: BOGO on Juice SKUs**
```json
{
  "code": "JUICE-BOGO",
  "is_stackable": true,
  "conditions": [
    {
      "condition_type": "has_any_sku",
      "parameters": {"skus": ["JUICE-001", "JUICE-002"]}
    }
  ],
  "actions": [
    {
      "action_type": "bogo",
      "parameters": {"skus": ["JUICE-001", "JUICE-002"]}
    }
  ]
}
```

**Example: Free Gift with SKU**
```json
{
  "code": "GIFT-CAMPAIGN",
  "conditions": [
    {"condition_type": "has_sku", "parameters": {"sku": "PREMIUM-001"}}
  ],
  "actions": [
    {"action_type": "free_item", "parameters": {"sku": "GIFT-KEYCHAIN", "quantity": 1}}
  ]
}
```

### Condition Types

| Type | Parameters | Description |
|---|---|---|
| `min_order_amount` | `{"min_amount": 500}` | Order subtotal ≥ min_amount |
| `has_sku` | `{"sku": "ABC-001"}` | Order contains this specific SKU |
| `has_any_sku` | `{"skus": ["A", "B"]}` | Order contains any of these SKUs |

### Action Types

| Type | Parameters | Description |
|---|---|---|
| `percentage_off_order` | `{"percentage": 10}` | Apply % discount to order total |
| `free_item` | `{"sku": "X", "quantity": 1}` | Inject free item lines into order |
| `bogo` | `{"skus": ["A", "B"]}` | Buy N, Get N/2 free (floor division) |

### Stacking Rules
- `is_stackable: true` — All stackable promotions apply simultaneously
- `is_stackable: false` — Only the best (highest discount) non-stackable promotion applies

---

## Sync
```
GET /sync/?since_version=42
```

Returns all orders with `sync_version > 42`.

---

## Order Status Flow

```
draft → submitted → confirmed → dispatched → delivered
                ↓
        exception_review_required
                ↓
        (manual review) → confirmed/cancelled
```
