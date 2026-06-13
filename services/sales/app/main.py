"""
Sales Service — FastAPI Application
Handles: Orders, Invoices, Sales Returns, Promotions + Rule Engine.
"""
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import (
    Column, String, Boolean, DateTime, Enum, ForeignKey,
    Float, Integer, func, Index, Text
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy import select
import enum

from app.engine.rule_engine import (
    OrderContext, OrderItem, evaluate_promotions
)
from app.engine.tax_calculator import calculate_taxes

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/sales_db"
    REDIS_URL: str = "redis://localhost:6379/2"
    PULSAR_SERVICE_URL: Optional[str] = None
    JWT_PUBLIC_KEY_PATH: str = "../../infra/keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DEBUG: bool = False

    @property
    def jwt_public_key(self) -> str:
        with open(self.JWT_PUBLIC_KEY_PATH) as f:
            return f.read()

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

settings = get_settings()
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class OrderStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    confirmed = "confirmed"
    exception_review_required = "exception_review_required"
    dispatched = "dispatched"
    delivered = "delivered"
    cancelled = "cancelled"
    returned = "returned"


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    credit = "credit"
    upi = "upi"
    bank_transfer = "bank_transfer"
    cheque = "cheque"


class ConditionType(str, enum.Enum):
    min_order_amount = "min_order_amount"
    has_sku = "has_sku"
    has_any_sku = "has_any_sku"


class ActionType(str, enum.Enum):
    percentage_off_order = "percentage_off_order"
    free_item = "free_item"
    bogo = "bogo"


class Order(Base):
    __tablename__ = "orders"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_uuid = Column(PGUUID(as_uuid=True), unique=True, nullable=True, index=True)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    customer_id = Column(PGUUID(as_uuid=True), nullable=False)
    sales_rep_id = Column(PGUUID(as_uuid=True), nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.draft, nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=True)
    subtotal = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    tax_amount = Column(Float, default=0.0)
    grand_total = Column(Float, default=0.0)
    client_total = Column(Float, nullable=True)   # What the client calculated
    exception_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    sync_version = Column(Integer, default=0, nullable=False)
    ordered_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrderLine(Base):
    __tablename__ = "order_lines"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(100), nullable=False)
    product_id = Column(PGUUID(as_uuid=True), nullable=True)
    name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    tax_rate_percent = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    subtotal = Column(Float, nullable=False)
    is_free_item = Column(Boolean, default=False)
    free_item_reason = Column(String(255), nullable=True)


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    code = Column(String(100), nullable=True)
    is_stackable = Column(Boolean, default=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PromotionCondition(Base):
    __tablename__ = "promotion_conditions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_id = Column(PGUUID(as_uuid=True), ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False)
    condition_type = Column(Enum(ConditionType), nullable=False)
    parameters = Column(JSONB, default={})


class PromotionAction(Base):
    __tablename__ = "promotion_actions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_id = Column(PGUUID(as_uuid=True), ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(Enum(ActionType), nullable=False)
    parameters = Column(JSONB, default={})


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    grand_total = Column(Float, nullable=False)
    tax_breakdown = Column(JSONB, default={})
    issued_at = Column(DateTime(timezone=True), server_default=func.now())
    due_date = Column(DateTime(timezone=True), nullable=True)
    is_paid = Column(Boolean, default=False)


# ─────────────────────────────────────────────
# AUTH DEPENDENCY
# ─────────────────────────────────────────────

from fastapi.security import HTTPBearer
from fastapi import Depends
from jose import jwt, JWTError
from fastapi.security import HTTPAuthorizationCredentials

bearer = HTTPBearer()


class TokenPayload(BaseModel):
    sub: str
    tenant_id: str
    roles: list[str]
    exp: int
    iat: int
    jti: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> TokenPayload:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_public_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TokenPayload(**payload)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=str(e))


# ─────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────

class OrderLineIn(BaseModel):
    sku: str
    product_id: Optional[UUID] = None
    name: str
    quantity: float
    unit_price: float
    tax_rate_percent: float = 0.0

    @field_validator("quantity", "unit_price")
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Must be > 0")
        return v


class OrderCreate(BaseModel):
    client_uuid: Optional[UUID] = None
    customer_id: UUID
    payment_method: Optional[PaymentMethod] = None
    items: list[OrderLineIn]
    promotion_codes: list[str] = []
    client_total: Optional[float] = None  # Client-calculated total for re-validation
    notes: Optional[str] = None
    is_intra_state: bool = True


class OrderResponse(BaseModel):
    id: UUID
    client_uuid: Optional[UUID]
    tenant_id: UUID
    customer_id: UUID
    status: OrderStatus
    subtotal: float
    discount_amount: float
    tax_amount: float
    grand_total: float
    exception_reason: Optional[str]
    sync_version: int
    created_at: datetime
    model_config = {"from_attributes": True}


class PromotionCreate(BaseModel):
    code: Optional[str] = None
    is_stackable: bool = False
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    conditions: list[dict]
    actions: list[dict]


class PromotionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    code: Optional[str]
    is_stackable: bool
    is_active: bool
    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# PUBLISHER
# ─────────────────────────────────────────────

from app.events.publisher import PulsarEventPublisher

publisher: PulsarEventPublisher = None


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global publisher
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    publisher = PulsarEventPublisher(settings.PULSAR_SERVICE_URL)
    logger.info("✅ Sales Service ready")
    yield
    publisher.close()
    await engine.dispose()


app = FastAPI(
    title="Sales Service",
    description="Order lifecycle, invoicing, sales returns, and promotion rule engine.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

REVALIDATION_TOLERANCE = 0.01  # 1% tolerance for client vs server total mismatch


# ─────────────────────────────────────────────
# ORDER ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/orders/", response_model=OrderResponse, status_code=201, tags=["Orders"])
async def create_order(
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Submit an order. Server re-validates totals using the rule engine.
    If client_total differs by >1%, order is flagged as exception_review_required.
    Idempotent via client_uuid.
    """
    # Idempotent check
    if data.client_uuid:
        existing = await db.execute(select(Order).where(Order.client_uuid == data.client_uuid))
        if existing.scalar_one_or_none():
            return existing.scalar_one_or_none()

    # Build order context for rule engine
    order_items = [
        OrderItem(sku=item.sku, quantity=item.quantity, unit_price=item.unit_price)
        for item in data.items
    ]
    context = OrderContext(
        items=order_items,
        tenant_id=current_user.tenant_id,
        customer_id=str(data.customer_id),
    )

    # Load active promotions matching given codes
    promotions_data = []
    if data.promotion_codes:
        promo_result = await db.execute(
            select(Promotion).where(
                Promotion.tenant_id == current_user.tenant_id,
                Promotion.code.in_(data.promotion_codes),
                Promotion.is_active == True,
            )
        )
        for promo in promo_result.scalars().all():
            conds = await db.execute(
                select(PromotionCondition).where(PromotionCondition.promotion_id == promo.id)
            )
            acts = await db.execute(
                select(PromotionAction).where(PromotionAction.promotion_id == promo.id)
            )
            promotions_data.append({
                "promotion": {"id": str(promo.id), "code": promo.code, "is_stackable": promo.is_stackable},
                "conditions": [{"condition_type": c.condition_type.value, "parameters": c.parameters}
                                for c in conds.scalars().all()],
                "actions": [{"action_type": a.action_type.value, "parameters": a.parameters}
                            for a in acts.scalars().all()],
            })

    # Evaluate promotions
    promo_results = evaluate_promotions(promotions_data, context)
    total_discount = sum(r.discount_amount for r in promo_results)

    # Calculate taxes
    tax_items = [
        {"sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price,
         "tax_rate_percent": data.items[idx].tax_rate_percent}
        for idx, i in enumerate(order_items)
    ]
    tax_summary = calculate_taxes(tax_items, discount_amount=total_discount,
                                   is_intra_state=data.is_intra_state)

    # Server re-validation
    order_status = OrderStatus.submitted
    exception_reason = None
    if data.client_total is not None:
        diff_pct = abs(data.client_total - tax_summary.grand_total) / max(tax_summary.grand_total, 1)
        if diff_pct > REVALIDATION_TOLERANCE:
            order_status = OrderStatus.exception_review_required
            exception_reason = (
                f"Client total {data.client_total:.2f} differs from server total "
                f"{tax_summary.grand_total:.2f} by {diff_pct*100:.2f}%"
            )

    order = Order(
        client_uuid=data.client_uuid,
        tenant_id=current_user.tenant_id,
        customer_id=data.customer_id,
        sales_rep_id=current_user.sub,
        status=order_status,
        payment_method=data.payment_method,
        subtotal=tax_summary.subtotal,
        discount_amount=tax_summary.total_discount,
        tax_amount=tax_summary.total_tax,
        grand_total=tax_summary.grand_total,
        client_total=data.client_total,
        exception_reason=exception_reason,
        notes=data.notes,
    )
    db.add(order)
    await db.flush()

    # Add order lines
    for item in data.items:
        line = OrderLine(
            order_id=order.id,
            sku=item.sku,
            product_id=item.product_id,
            name=item.name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            tax_rate_percent=item.tax_rate_percent,
            subtotal=item.quantity * item.unit_price,
        )
        db.add(line)

    # Add free items from promotions
    for promo_result in promo_results:
        for free_item in promo_result.free_items:
            line = OrderLine(
                order_id=order.id,
                sku=free_item["sku"],
                name=f"FREE: {free_item['sku']}",
                quantity=free_item["quantity"],
                unit_price=0.0,
                subtotal=0.0,
                is_free_item=True,
                free_item_reason=free_item.get("reason"),
            )
            db.add(line)

    await db.commit()
    await db.refresh(order)

    # Publish event
    if publisher:
        publisher.order_created(
            str(order.id), current_user.tenant_id,
            str(data.customer_id), tax_summary.grand_total
        )

    return order


@app.get("/orders/", response_model=list[OrderResponse], tags=["Orders"])
async def list_orders(
    customer_id: Optional[UUID] = Query(None),
    status: Optional[OrderStatus] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    query = select(Order).where(Order.tenant_id == current_user.tenant_id)
    if customer_id:
        query = query.where(Order.customer_id == customer_id)
    if status:
        query = query.where(Order.status == status)
    result = await db.execute(query.offset(skip).limit(limit).order_by(Order.created_at.desc()))
    return result.scalars().all()


@app.get("/orders/{order_id}", response_model=OrderResponse, tags=["Orders"])
async def get_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.tenant_id == current_user.tenant_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.patch("/orders/{order_id}/confirm", response_model=OrderResponse, tags=["Orders"])
async def confirm_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = OrderStatus.confirmed
    await db.commit()
    await db.refresh(order)
    if publisher:
        publisher.order_confirmed(str(order.id), current_user.tenant_id)
    return order


# ─────────────────────────────────────────────
# PROMOTION ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/promotions/", response_model=PromotionResponse, status_code=201, tags=["Promotions"])
async def create_promotion(
    data: PromotionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    promo = Promotion(
        tenant_id=current_user.tenant_id,
        code=data.code,
        is_stackable=data.is_stackable,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )
    db.add(promo)
    await db.flush()

    for cond in data.conditions:
        db.add(PromotionCondition(
            promotion_id=promo.id,
            condition_type=cond["condition_type"],
            parameters=cond.get("parameters", {}),
        ))
    for act in data.actions:
        db.add(PromotionAction(
            promotion_id=promo.id,
            action_type=act["action_type"],
            parameters=act.get("parameters", {}),
        ))

    await db.commit()
    await db.refresh(promo)
    return promo


@app.get("/promotions/", response_model=list[PromotionResponse], tags=["Promotions"])
async def list_promotions(
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(Promotion).where(
            Promotion.tenant_id == current_user.tenant_id,
            Promotion.is_active == True,
        )
    )
    return result.scalars().all()


@app.get("/sync/", tags=["Sync"])
async def sync_orders(
    since_version: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(Order).where(
            Order.tenant_id == current_user.tenant_id,
            Order.sync_version > since_version,
        )
    )
    orders = result.scalars().all()
    max_v = max((o.sync_version for o in orders), default=since_version)
    return {"domain": "sales", "watermark": max_v, "orders": [OrderResponse.model_validate(o) for o in orders]}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "sales"}
