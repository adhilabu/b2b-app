"""
Pulsar Event Consumer for Notification Service.
Subscribes to all domain event topics and dispatches notifications to connected users.
"""
import asyncio
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Maps Pulsar topic → notification template config
TOPIC_NOTIFICATION_MAP = {
    "persistent://b2b/events/order-created": {
        "title": "New Order Submitted",
        "body_template": "Order for customer {customer_id} has been submitted.",
        "channels": ["websocket", "fcm"],
    },
    "persistent://b2b/events/order-confirmed": {
        "title": "Order Confirmed",
        "body_template": "Your order has been confirmed and is being processed.",
        "channels": ["websocket", "fcm"],
    },
    "persistent://b2b/events/order-rejected": {
        "title": "Order Requires Attention",
        "body_template": "An order requires review due to a pricing exception.",
        "channels": ["websocket", "fcm"],
    },
    "persistent://b2b/events/beat-plan-optimized": {
        "title": "Route Optimized",
        "body_template": "Your beat plan has been optimized for efficient delivery.",
        "channels": ["websocket"],
    },
    "persistent://b2b/events/attendance-checked-in": {
        "title": "Check-in Confirmed",
        "body_template": "Your attendance has been recorded.",
        "channels": ["websocket"],
    },
}


class PulsarEventConsumer:
    """
    Consumes events from Apache Pulsar topics and dispatches WebSocket notifications.
    Starts gracefully — no-op if Pulsar URL is not configured or pulsar-client not installed.
    """

    def __init__(self, pulsar_url: Optional[str], connection_manager: Any = None):
        self.pulsar_url = pulsar_url
        self.connection_manager = connection_manager
        self._client = None
        self._consumers: list[tuple[str, Any]] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start subscribing to Pulsar topics in the background."""
        if not self.pulsar_url:
            logger.info("PULSAR_SERVICE_URL not set — event consumer disabled")
            return

        try:
            import pulsar  # type: ignore
        except ImportError:
            logger.warning("pulsar-client not installed — event consumer disabled")
            return

        try:
            self._client = pulsar.Client(self.pulsar_url)
            for topic in TOPIC_NOTIFICATION_MAP:
                try:
                    consumer = self._client.subscribe(
                        topic,
                        subscription_name="notification-service",
                        consumer_type=pulsar.ConsumerType.Shared,
                    )
                    self._consumers.append((topic, consumer))
                    logger.info(f"Subscribed to Pulsar topic: {topic}")
                except Exception as e:
                    logger.warning(f"Could not subscribe to {topic}: {e}")

            self._running = True
            self._task = asyncio.create_task(self._consume_loop())
            logger.info("Pulsar event consumer started")
        except Exception as e:
            logger.warning(f"Pulsar consumer startup failed: {e}")

    async def _consume_loop(self) -> None:
        """Background loop — poll each consumer and dispatch notifications."""
        while self._running:
            for topic, consumer in self._consumers:
                try:
                    msg = consumer.receive(timeout_millis=100)
                    await self._process_message(topic, msg)
                    consumer.acknowledge(msg)
                except Exception:
                    # Timeout is expected — swallow silently
                    pass
            await asyncio.sleep(0.05)

    async def _process_message(self, topic: str, msg: Any) -> None:
        """Parse a Pulsar message and send a WebSocket notification to the relevant user."""
        try:
            data = json.loads(msg.data().decode("utf-8"))
            config = TOPIC_NOTIFICATION_MAP.get(topic)
            if not config:
                return

            body = config["body_template"].format(**{k: data.get(k, "") for k in data})
            title = config["title"]

            # Resolve target user — events include sales_rep_id, driver_id, or user_id
            user_id = (
                data.get("user_id")
                or data.get("sales_rep_id")
                or data.get("driver_id")
            )
            if user_id and self.connection_manager:
                await self.connection_manager.send_notification(
                    user_id=str(user_id),
                    payload={"title": title, "body": body, "event_data": data},
                )
        except Exception as e:
            logger.error(f"Error processing Pulsar message from {topic}: {e}")

    async def stop(self) -> None:
        """Gracefully stop the consumer loop and close connections."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        for _, consumer in self._consumers:
            try:
                consumer.close()
            except Exception:
                pass

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass

        logger.info("Pulsar event consumer stopped")
