-- =============================================================================
-- DSD B2B SaaS — PostgreSQL Initialization
-- Creates one database per microservice
-- Executed automatically on first container start
-- =============================================================================

-- Identity Service Database
CREATE DATABASE identity_db;

-- Catalog Service Database
CREATE DATABASE catalog_db;

-- Sales Service Database
CREATE DATABASE sales_db;

-- Route Service Database
CREATE DATABASE route_db;

-- Attendance Service Database
CREATE DATABASE attendance_db;

-- Notification Service Database
CREATE DATABASE notification_db;

-- Grant all privileges to admin user
GRANT ALL PRIVILEGES ON DATABASE identity_db TO b2b_admin;
GRANT ALL PRIVILEGES ON DATABASE catalog_db TO b2b_admin;
GRANT ALL PRIVILEGES ON DATABASE sales_db TO b2b_admin;
GRANT ALL PRIVILEGES ON DATABASE route_db TO b2b_admin;
GRANT ALL PRIVILEGES ON DATABASE attendance_db TO b2b_admin;
GRANT ALL PRIVILEGES ON DATABASE notification_db TO b2b_admin;

-- Enable UUID extension in each database
\c identity_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c catalog_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c sales_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ENUM types for Sales service (from architecture spec)
CREATE TYPE condition_type AS ENUM ('min_order_amount', 'has_sku', 'has_any_sku');
CREATE TYPE action_type AS ENUM ('percentage_off_order', 'free_item', 'bogo');
CREATE TYPE order_status AS ENUM (
  'draft', 'submitted', 'confirmed', 'exception_review_required',
  'dispatched', 'delivered', 'cancelled', 'returned'
);
CREATE TYPE payment_method AS ENUM ('cash', 'credit', 'upi', 'bank_transfer', 'cheque');

\c route_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c attendance_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE leave_status AS ENUM ('pending', 'approved', 'rejected', 'cancelled');
CREATE TYPE leave_type AS ENUM ('casual', 'sick', 'earned', 'unpaid', 'maternity', 'paternity');
CREATE TYPE attendance_status AS ENUM ('present', 'absent', 'on_leave', 'half_day', 'work_from_home');

\c notification_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
