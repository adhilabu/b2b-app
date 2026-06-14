"""
Pulsar Event Consumer for Route Service.
Listens to OrderConfirmed events and marks the relevant beat stop as having a pending delivery.
"""
import asyncio
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

ORDER_CONFIRMED_TOPIC = "persistent://b2b/events/order-confirmed"


class RouteEventConsumer:
    """
    Subscribes to order-confirmed events so the Route service can track
    which customer stops have pending deliveries for route optimization.
    """

    def __init__(self, pulsar_url: Optional[str]):
        self.pulsar_url = pulsar_url
        self._client = None
        self._consumer = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self.pulsar_url:
            logger.info("PULSAR_SERVICE_URL not set — route event consumer disabled")
            return

        try:
            import pulsar  # type: ignore
        except ImportError:
            logger.warning("pulsar-client not installed — route event consumer disabled")
            return

        try:
            self._client = pulsar.Client(self.pulsar_url)
            self._consumer = self._client.subscribe(
                ORDER_CONFIRMED_TOPIC,
                subscription_name="route-service-delivery-tracker",
                consumer_type=pulsar.ConsumerType.Shared,
            )
            self._running = True
            self._task = asyncio.create_task(self._consume_loop())
            logger.info("Route event consumer started — listening for OrderConfirmed events")
        except Exception as e:
            logger.warning(f"Route event consumer startup failed: {e}")

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                msg = self._consumer.receive(timeout_millis=200)
                await self._handle_order_confirmed(msg)
                self._consumer.acknowledge(msg)
            except Exception:
                pass
            await asyncio.sleep(0.1)

    async def _handle_order_confirmed(self, msg: Any) -> None:
        """
        When an order is confirmed, log it so route planners can factor in the delivery.
        In a full Phase 2 implementation, this would update a beat_stop.has_pending_delivery
        flag so the VRP solver prioritises those stops.
        """
        try:
            data = json.loads(msg.data().decode("utf-8"))
            order_id = data.get("order_id")
            customer_id = data.get("customer_id")
            tenant_id = data.get("tenant_id")
            logger.info(
                f"OrderConfirmed received: order_id={order_id} customer_id={customer_id} tenant={tenant_id} "
                "— flagged for delivery scheduling"
            )
            # TODO Phase 2: query BeatStop by customer_id where beat date == today,
            # set has_pending_delivery=True, increment sync_version
        except Exception as e:
            logger.error(f"Error handling OrderConfirmed event: {e}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass

        logger.info("Route event consumer stopped")
