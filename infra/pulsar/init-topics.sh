#!/bin/bash
# =============================================================================
# Apache Pulsar — Topic Initialization
# Creates all required persistent topics for the B2B SaaS platform
# =============================================================================

set -e

PULSAR_ADMIN="bin/pulsar-admin"
TENANT="b2b"
NAMESPACE="events"

echo "⏳ Waiting for Pulsar to be ready..."
until curl -sf http://localhost:8080/admin/v2/brokers/health; do
  sleep 5
done
echo "✅ Pulsar is ready"

# Create tenant and namespace
echo "📦 Creating tenant and namespace..."
$PULSAR_ADMIN tenants create $TENANT \
  --allowed-clusters standalone 2>/dev/null || echo "Tenant already exists"

$PULSAR_ADMIN namespaces create $TENANT/$NAMESPACE 2>/dev/null || echo "Namespace already exists"

# Set retention (keep messages for 7 days or 5GB)
$PULSAR_ADMIN namespaces set-retention $TENANT/$NAMESPACE \
  --size 5120 --time 10080

# ─────────────────────────────────────────────
# DOMAIN TOPICS
# ─────────────────────────────────────────────

TOPICS=(
  # Identity & Customer
  "persistent://$TENANT/$NAMESPACE/customer-created"
  "persistent://$TENANT/$NAMESPACE/customer-updated"

  # Sales & Orders
  "persistent://$TENANT/$NAMESPACE/order-created"
  "persistent://$TENANT/$NAMESPACE/order-confirmed"
  "persistent://$TENANT/$NAMESPACE/order-rejected"
  "persistent://$TENANT/$NAMESPACE/order-exception"
  "persistent://$TENANT/$NAMESPACE/invoice-generated"
  "persistent://$TENANT/$NAMESPACE/sales-return-created"

  # Promotions
  "persistent://$TENANT/$NAMESPACE/promotion-applied"

  # Route & Beat
  "persistent://$TENANT/$NAMESPACE/beat-plan-created"
  "persistent://$TENANT/$NAMESPACE/route-optimized"

  # Attendance
  "persistent://$TENANT/$NAMESPACE/attendance-logged"
  "persistent://$TENANT/$NAMESPACE/leave-approved"
  "persistent://$TENANT/$NAMESPACE/leave-rejected"

  # Sync
  "persistent://$TENANT/$NAMESPACE/sync-push-received"
)

echo "📨 Creating Pulsar topics..."
for topic in "${TOPICS[@]}"; do
  $PULSAR_ADMIN topics create "$topic" 2>/dev/null || echo "  ↪ Topic already exists: $topic"
  echo "  ✅ $topic"
done

echo ""
echo "✅ All Pulsar topics initialized"
