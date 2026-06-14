"""
Sync Events Router — handles offline events forwarded by the Orchestration service.
Supports: CustomerCreated, CustomerUpdated (idempotent upserts via client_uuid).
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import Customer, User
from app.auth.dependencies import get_current_active_user

router = APIRouter(prefix="/sync", tags=["Sync"])


class SyncEventIn(BaseModel):
    event_id: str
    event_type: str
    domain: str
    timestamp: Optional[int] = None
    payload: dict = {}


@router.post("/events")
async def receive_sync_event(
    event: SyncEventIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Receives offline identity domain events from the Orchestration service.

    Handles:
    - CustomerCreated / CustomerUpdated — idempotent upsert by client_uuid
    """
    if event.event_type in ("CustomerCreated", "CustomerUpdated"):
        payload = event.payload
        client_uuid_str = payload.get("client_uuid")

        # Try idempotent lookup first
        customer = None
        if client_uuid_str:
            try:
                client_uuid = UUID(client_uuid_str)
                existing = await db.execute(
                    select(Customer).where(Customer.client_uuid == client_uuid)
                )
                customer = existing.scalar_one_or_none()
            except ValueError:
                pass

        if customer is None:
            # Create new customer
            customer = Customer(
                client_uuid=UUID(client_uuid_str) if client_uuid_str else None,
                tenant_id=current_user.tenant_id,
                assigned_rep_id=payload.get("assigned_rep_id"),
                name=payload.get("name", "Unknown"),
                code=payload.get("code"),
                contact_person=payload.get("contact_person"),
                phone=payload.get("phone"),
                email=payload.get("email"),
                address_line1=payload.get("address_line1"),
                address_line2=payload.get("address_line2"),
                city=payload.get("city"),
                state=payload.get("state"),
                pincode=payload.get("pincode"),
                country=payload.get("country", "India"),
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
                is_active=payload.get("is_active", True),
                credit_limit=payload.get("credit_limit", 0.0),
            )
            db.add(customer)
        else:
            # Update existing — Last-Write-Wins
            for field in ("name", "code", "contact_person", "phone", "email",
                          "address_line1", "address_line2", "city", "state",
                          "pincode", "latitude", "longitude", "credit_limit", "is_active"):
                if field in payload and payload[field] is not None:
                    setattr(customer, field, payload[field])

        await db.commit()
        return {"event_id": event.event_id, "status": "accepted", "customer_id": str(customer.id)}

    return {"event_id": event.event_id, "status": "skipped", "reason": f"unhandled event type: {event.event_type}"}
