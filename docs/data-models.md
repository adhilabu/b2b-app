# Data Models — Database Schemas

## Database-per-Service Strategy

Each microservice has its own isolated PostgreSQL database. No cross-database foreign keys exist — references are **logical** (UUID values only), not enforced by DB constraints across services.

```
PostgreSQL Instance
├── identity_db      ← Users, Tenants, Customers
├── catalog_db       ← Categories, Products, Prices
├── sales_db         ← Orders, OrderLines, Invoices, Promotions
├── route_db         ← Beats, BeatStops
├── attendance_db    ← Attendance, LeaveRequests
└── notification_db  ← WebPushSubscriptions, NotificationLogs
```

---

## Identity Service (identity_db)

### tenants
```sql
CREATE TABLE tenants (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       VARCHAR(255) NOT NULL,
  slug       VARCHAR(100) UNIQUE NOT NULL,
  is_active  BOOLEAN DEFAULT true,
  settings   JSONB DEFAULT '{}',
  -- e.g., {"currency": "INR", "timezone": "Asia/Kolkata",
  --         "hq_lat": 12.97, "hq_lon": 77.59, "geo_fence_radius_km": 5}
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### users
```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  email           VARCHAR(255) UNIQUE NOT NULL,
  phone           VARCHAR(20),
  full_name       VARCHAR(255) NOT NULL,
  hashed_password VARCHAR(255) NOT NULL,
  role            VARCHAR(50) NOT NULL,  -- admin|manager|supervisor|sales_rep|driver
  is_active       BOOLEAN DEFAULT true,
  is_verified     BOOLEAN DEFAULT false,
  last_login_at   TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (tenant_id, email)
);
```

### customers
```sql
CREATE TABLE customers (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_uuid       UUID UNIQUE,             -- Offline-generated UUID
  tenant_id         UUID NOT NULL,
  assigned_rep_id   UUID,                    -- Logical ref to users.id
  name              VARCHAR(255) NOT NULL,
  code              VARCHAR(100),
  contact_person    VARCHAR(255),
  phone             VARCHAR(20),
  email             VARCHAR(255),
  address_line1     VARCHAR(255),
  address_line2     VARCHAR(255),
  city              VARCHAR(100),
  state             VARCHAR(100),
  pincode           VARCHAR(20),
  country           VARCHAR(100) DEFAULT 'India',
  latitude          FLOAT,
  longitude         FLOAT,
  geo_verified      BOOLEAN DEFAULT false,
  is_active         BOOLEAN DEFAULT true,
  credit_limit      FLOAT DEFAULT 0.0,
  payment_terms_days VARCHAR(50),
  sync_version      BIGINT DEFAULT 0,
  created_at        TIMESTAMPTZ DEFAULT now(),
  updated_at        TIMESTAMPTZ DEFAULT now()
);
```

---

## Catalog Service (catalog_db)

### categories
```sql
CREATE TABLE categories (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL,
  parent_id   UUID REFERENCES categories(id),  -- Self-referential tree
  name        VARCHAR(255) NOT NULL,
  slug        VARCHAR(255) NOT NULL,
  description TEXT,
  image_url   VARCHAR(500),
  sort_order  INT DEFAULT 0,
  is_active   BOOLEAN DEFAULT true,
  sync_version INT DEFAULT 0,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (tenant_id, slug)
);
```

### products
```sql
CREATE TABLE products (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL,
  category_id      UUID REFERENCES categories(id),
  sku              VARCHAR(100) NOT NULL,
  name             VARCHAR(255) NOT NULL,
  description      TEXT,
  barcode          VARCHAR(100),
  uom              VARCHAR(50) DEFAULT 'piece',
  pack_size        FLOAT DEFAULT 1.0,
  weight_kg        FLOAT,
  status           VARCHAR(50) DEFAULT 'active',   -- active|inactive|discontinued
  is_taxable       BOOLEAN DEFAULT true,
  tax_rate_percent FLOAT DEFAULT 0.0,
  hsn_code         VARCHAR(20),                    -- India GST HSN code
  image_urls       JSONB DEFAULT '[]',
  attributes       JSONB DEFAULT '{}',
  -- e.g., {"flavor": "mango", "volume_ml": 200}
  sync_version     INT DEFAULT 0,
  created_at       TIMESTAMPTZ DEFAULT now(),
  updated_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (tenant_id, sku)
);
```

### product_prices
```sql
CREATE TABLE product_prices (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id      UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL,
  price_list_name VARCHAR(100) DEFAULT 'standard',
  -- e.g., "standard", "wholesale", "vip"
  unit_price      FLOAT NOT NULL CHECK (unit_price > 0),
  min_quantity    INT DEFAULT 1,
  currency        VARCHAR(10) DEFAULT 'INR',
  is_active       BOOLEAN DEFAULT true,
  valid_from      TIMESTAMPTZ,
  valid_to        TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (product_id, price_list_name, min_quantity)
);
```

---

## Sales Service (sales_db)

### orders
```sql
CREATE TABLE orders (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_uuid     UUID UNIQUE,              -- Offline idempotency key
  tenant_id       UUID NOT NULL,
  customer_id     UUID NOT NULL,            -- Logical ref to customers.id
  sales_rep_id    UUID NOT NULL,            -- Logical ref to users.id
  status          order_status NOT NULL DEFAULT 'draft',
  payment_method  payment_method,
  subtotal        FLOAT DEFAULT 0,
  discount_amount FLOAT DEFAULT 0,
  tax_amount      FLOAT DEFAULT 0,
  grand_total     FLOAT DEFAULT 0,
  client_total    FLOAT,                    -- What client calculated (for re-validation)
  exception_reason TEXT,
  notes           TEXT,
  sync_version    INT DEFAULT 0,
  ordered_at      TIMESTAMPTZ DEFAULT now(),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TYPE order_status AS ENUM (
  'draft', 'submitted', 'confirmed', 'exception_review_required',
  'dispatched', 'delivered', 'cancelled', 'returned'
);

CREATE TYPE payment_method AS ENUM (
  'cash', 'credit', 'upi', 'bank_transfer', 'cheque'
);
```

### order_lines
```sql
CREATE TABLE order_lines (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id         UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  sku              VARCHAR(100) NOT NULL,
  product_id       UUID,
  name             VARCHAR(255) NOT NULL,
  quantity         FLOAT NOT NULL,
  unit_price       FLOAT NOT NULL,
  tax_rate_percent FLOAT DEFAULT 0,
  discount_amount  FLOAT DEFAULT 0,
  subtotal         FLOAT NOT NULL,
  is_free_item     BOOLEAN DEFAULT false,
  free_item_reason VARCHAR(255)
);
```

### promotions (from architecture spec)
```sql
CREATE TYPE condition_type AS ENUM ('min_order_amount', 'has_sku', 'has_any_sku');
CREATE TYPE action_type AS ENUM ('percentage_off_order', 'free_item', 'bogo');

CREATE TABLE promotions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL,
  code         VARCHAR(100),
  is_stackable BOOLEAN DEFAULT false,
  valid_from   TIMESTAMPTZ,
  valid_to     TIMESTAMPTZ,
  is_active    BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE promotion_conditions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  promotion_id   UUID NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
  condition_type condition_type NOT NULL,
  parameters     JSONB DEFAULT '{}'
  -- e.g., {"min_amount": 500}, {"sku": "JUICE-001"}, {"skus": ["A", "B"]}
);

CREATE TABLE promotion_actions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  promotion_id UUID NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
  action_type  action_type NOT NULL,
  parameters   JSONB DEFAULT '{}'
  -- e.g., {"percentage": 10}, {"sku": "GIFT-001", "quantity": 1}
);
```

---

## Route Service (route_db)

```sql
CREATE TABLE beats (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL,
  user_id        UUID NOT NULL,             -- Logical ref to users.id
  name           VARCHAR(255) NOT NULL,
  scheduled_date DATE NOT NULL,
  is_optimized   BOOLEAN DEFAULT false,
  sync_version   BIGINT DEFAULT 0,
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE beat_stops (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  beat_id                 UUID NOT NULL REFERENCES beats(id) ON DELETE CASCADE,
  customer_id             UUID NOT NULL,    -- Logical ref to customers.id
  customer_name           VARCHAR(255),
  latitude                FLOAT,
  longitude               FLOAT,
  sequence                INT NOT NULL,     -- Optimized visit order (0-indexed)
  estimated_visit_minutes INT DEFAULT 15,
  visit_notes             VARCHAR(500)
);
```

---

## Attendance Service (attendance_db)

```sql
CREATE TYPE attendance_status AS ENUM (
  'present', 'absent', 'on_leave', 'half_day', 'work_from_home'
);

CREATE TYPE leave_type AS ENUM (
  'casual', 'sick', 'earned', 'unpaid'
);

CREATE TYPE leave_status AS ENUM (
  'pending', 'approved', 'rejected', 'cancelled'
);

CREATE TABLE attendance (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  user_id         UUID NOT NULL,
  attendance_date DATE NOT NULL,
  status          attendance_status DEFAULT 'present',
  check_in_at     TIMESTAMPTZ,
  check_out_at    TIMESTAMPTZ,
  check_in_lat    VARCHAR(20),
  check_in_lon    VARCHAR(20),
  check_out_lat   VARCHAR(20),
  check_out_lon   VARCHAR(20),
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id, attendance_date)
);

CREATE TABLE leave_requests (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL,
  user_id     UUID NOT NULL,
  leave_type  leave_type NOT NULL,
  from_date   DATE NOT NULL,
  to_date     DATE NOT NULL,
  reason      TEXT,
  status      leave_status DEFAULT 'pending',
  approved_by UUID,
  approved_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

---

## Notification Service (notification_db)

```sql
CREATE TABLE web_push_subscriptions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    VARCHAR(255) NOT NULL,
  tenant_id  VARCHAR(255) NOT NULL,
  endpoint   TEXT NOT NULL UNIQUE,
  keys       JSONB NOT NULL,
  -- {"p256dh": "...", "auth": "..."}
  user_agent VARCHAR(500),
  is_active  BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE notification_logs (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    VARCHAR(255),
  tenant_id  VARCHAR(255),
  channel    VARCHAR(50) NOT NULL,  -- websocket|fcm|webpush|email
  event_type VARCHAR(100) NOT NULL,
  title      VARCHAR(255),
  body       TEXT,
  is_sent    BOOLEAN DEFAULT false,
  error      TEXT,
  sent_at    TIMESTAMPTZ DEFAULT now()
);
```
