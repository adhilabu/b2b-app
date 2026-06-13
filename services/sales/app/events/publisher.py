"""
Pulsar Event Publisher for the Sales Service.
Publishes domain events to Apache Pulsar topics.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TENANT = "b2b"
NAMESPACE = "events"


def _topic(name: str) -> str:
    return f"persistent://{TENANT}/{NAMESPACE}/{name}"


class PulsarEventPublisher:
    """
    Wraps a Pulsar client and exposes domain-event publish methods.
    In test mode (no Pulsar URL), falls back to a no-op stub.
    """

    def __init__(self, pulsar_url: str | None):
        self._client = None
        self._producers: dict = {}

        if pulsar_url:
            try:
                import pulsar
                self._client = pulsar.Client(pulsar_url)
                logger.info(f"✅ Pulsar client connected: {pulsar_url}")
            except Exception as e:
                logger.warning(f"⚠️ Pulsar unavailable — events will be logged only: {e}")

    def _get_producer(self, topic: str):
        if not self._client:
            return None
        if topic not in self._producers:
            self._producers[topic] = self._client.create_producer(topic)
        return self._producers[topic]

    def _publish(self, topic_name: str, event: dict) -> None:
        event["_published_at"] = datetime.now(timezone.utc).isoformat()
        message = json.dumps(event).encode("utf-8")
        producer = self._get_producer(_topic(topic_name))
        if producer:
            producer.send(message)
            logger.debug(f"📨 Published to {topic_name}: {event}")
        else:
            logger.info(f"[STUB] Event {topic_name}: {event}")

    def order_created(self, order_id: str, tenant_id: str, customer_id: str, total: float):
        self._publish("order-created", {
            "event": "OrderCreated",
            "order_id": order_id,
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "total": total,
        })

    def order_confirmed(self, order_id: str, tenant_id: str):
        self._publish("order-confirmed", {
            "event": "OrderConfirmed",
            "order_id": order_id,
            "tenant_id": tenant_id,
        })

    def order_rejected(self, order_id: str, tenant_id: str, reason: str):
        self._publish("order-rejected", {
            "event": "OrderRejected",
            "order_id": order_id,
            "tenant_id": tenant_id,
            "reason": reason,
        })

    def invoice_generated(self, invoice_id: str, order_id: str, tenant_id: str):
        self._publish("invoice-generated", {
            "event": "InvoiceGenerated",
            "invoice_id": invoice_id,
            "order_id": order_id,
            "tenant_id": tenant_id,
        })

    def close(self):
        for producer in self._producers.values():
            producer.close()
        if self._client:
            self._client.close()
