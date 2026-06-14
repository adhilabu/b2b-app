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
        existing_order = existing.scalar_one_or_none()
        if existing_order:
            return existing_order

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


# ─────────────────────────────────────────────
# SALES RETURNS
# ─────────────────────────────────────────────

class ReturnStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class SalesReturn(Base):
    __tablename__ = "sales_returns"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_uuid = Column(PGUUID(as_uuid=True), unique=True, nullable=True, index=True)
    order_id = Column(PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    customer_id = Column(PGUUID(as_uuid=True), nullable=False)
    sales_rep_id = Column(PGUUID(as_uuid=True), nullable=False)
    return_reason = Column(Text, nullable=True)
    status = Column(Enum(ReturnStatus), default=ReturnStatus.pending, nullable=False)
    total_return_amount = Column(Float, default=0.0)
    sync_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SalesReturnLine(Base):
    __tablename__ = "sales_return_lines"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    return_id = Column(PGUUID(as_uuid=True), ForeignKey("sales_returns.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(100), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    return_amount = Column(Float, nullable=False)
    reason = Column(String(255), nullable=True)


class ReturnLineIn(BaseModel):
    sku: str
    quantity: float
    unit_price: float
    reason: Optional[str] = None


class SalesReturnCreate(BaseModel):
    client_uuid: Optional[UUID] = None
    order_id: UUID
    customer_id: UUID
    return_reason: Optional[str] = None
    items: list[ReturnLineIn]


class SalesReturnResponse(BaseModel):
    id: UUID
    client_uuid: Optional[UUID]
    order_id: UUID
    tenant_id: UUID
    customer_id: UUID
    status: ReturnStatus
    total_return_amount: float
    sync_version: int
    created_at: datetime
    model_config = {"from_attributes": True}


@app.post("/returns/", response_model=SalesReturnResponse, status_code=201, tags=["Sales Returns"])
async def create_sales_return(
    data: SalesReturnCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Create a sales return. Idempotent via client_uuid."""
    if data.client_uuid:
        existing = await db.execute(select(SalesReturn).where(SalesReturn.client_uuid == data.client_uuid))
        existing_return = existing.scalar_one_or_none()
        if existing_return:
            return existing_return

    # Verify the order exists and belongs to this tenant
    order_result = await db.execute(
        select(Order).where(Order.id == data.order_id, Order.tenant_id == current_user.tenant_id)
    )
    if not order_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Order not found")

    total_return_amount = sum(item.quantity * item.unit_price for item in data.items)

    sales_return = SalesReturn(
        client_uuid=data.client_uuid,
        order_id=data.order_id,
        tenant_id=current_user.tenant_id,
        customer_id=data.customer_id,
        sales_rep_id=current_user.sub,
        return_reason=data.return_reason,
        total_return_amount=total_return_amount,
    )
    db.add(sales_return)
    await db.flush()

    for item in data.items:
        db.add(SalesReturnLine(
            return_id=sales_return.id,
            sku=item.sku,
            quantity=item.quantity,
            unit_price=item.unit_price,
            return_amount=item.quantity * item.unit_price,
            reason=item.reason,
        ))

    await db.commit()
    await db.refresh(sales_return)
    return sales_return


@app.get("/returns/", response_model=list[SalesReturnResponse], tags=["Sales Returns"])
async def list_sales_returns(
    customer_id: Optional[UUID] = Query(None),
    status: Optional[ReturnStatus] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    query = select(SalesReturn).where(SalesReturn.tenant_id == current_user.tenant_id)
    if customer_id:
        query = query.where(SalesReturn.customer_id == customer_id)
    if status:
        query = query.where(SalesReturn.status == status)
    result = await db.execute(query.offset(skip).limit(limit).order_by(SalesReturn.created_at.desc()))
    return result.scalars().all()


@app.get("/returns/{return_id}", response_model=SalesReturnResponse, tags=["Sales Returns"])
async def get_sales_return(
    return_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(SalesReturn).where(SalesReturn.id == return_id, SalesReturn.tenant_id == current_user.tenant_id)
    )
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Sales return not found")
    return ret


@app.patch("/returns/{return_id}/approve", response_model=SalesReturnResponse, tags=["Sales Returns"])
async def approve_sales_return(
    return_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    if "manager" not in current_user.roles and "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Only managers and admins can approve returns")
    result = await db.execute(select(SalesReturn).where(SalesReturn.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Sales return not found")
    ret.status = ReturnStatus.approved
    await db.commit()
    await db.refresh(ret)
    return ret


@app.get("/sync/returns/", tags=["Sync"])
async def sync_returns(
    since_version: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(SalesReturn).where(
            SalesReturn.tenant_id == current_user.tenant_id,
            SalesReturn.sync_version > since_version,
        )
    )
    returns = result.scalars().all()
    max_v = max((r.sync_version for r in returns), default=since_version)
    return {"domain": "sales", "watermark": max_v, "returns": [SalesReturnResponse.model_validate(r) for r in returns]}


# ─────────────────────────────────────────────
# VAN SALES — PHASE 2
# ─────────────────────────────────────────────

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class VanStockStatus(str, enum.Enum):
    loaded = "loaded"
    partially_returned = "partially_returned"
    settled = "settled"


class VanStock(Base):
    """Virtual warehouse loaded onto a delivery van for a day."""
    __tablename__ = "van_stocks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    driver_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    beat_id = Column(PGUUID(as_uuid=True), nullable=True)
    load_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(Enum(VanStockStatus), default=VanStockStatus.loaded, nullable=False)
    invoice_prefix = Column(String(20), nullable=True)
    invoice_seq_start = Column(Integer, nullable=True)
    invoice_seq_end = Column(Integer, nullable=True)
    invoice_seq_current = Column(Integer, nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VanStockLine(Base):
    """Individual SKU loaded onto the van."""
    __tablename__ = "van_stock_lines"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    van_stock_id = Column(PGUUID(as_uuid=True), ForeignKey("van_stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(100), nullable=False)
    product_id = Column(PGUUID(as_uuid=True), nullable=True)
    loaded_quantity = Column(Float, nullable=False)
    sold_quantity = Column(Float, default=0.0, nullable=False)
    returned_quantity = Column(Float, default=0.0, nullable=False)


class VanSettlement(Base):
    """End-of-day cash and inventory reconciliation."""
    __tablename__ = "van_settlements"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    van_stock_id = Column(PGUUID(as_uuid=True), ForeignKey("van_stocks.id"), nullable=False, unique=True)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    driver_id = Column(PGUUID(as_uuid=True), nullable=False)
    total_invoiced = Column(Float, default=0.0)
    total_cash_collected = Column(Float, default=0.0)
    total_upi_collected = Column(Float, default=0.0)
    total_credit_given = Column(Float, default=0.0)
    cash_variance = Column(Float, default=0.0)
    inventory_variance = Column(JSONB, default={})
    status = Column(String(20), default="draft")
    notes = Column(Text, nullable=True)
    settled_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_by = Column(PGUUID(as_uuid=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)


# Van Sales Schemas
class VanStockLineIn(BaseModel):
    sku: str
    product_id: Optional[UUID] = None
    loaded_quantity: float


class VanStockCreate(BaseModel):
    driver_id: UUID
    beat_id: Optional[UUID] = None
    items: list[VanStockLineIn]
    invoice_prefix: Optional[str] = None


class VanStockLineResponse(BaseModel):
    id: UUID
    sku: str
    loaded_quantity: float
    sold_quantity: float
    returned_quantity: float
    model_config = {"from_attributes": True}


class VanStockResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    driver_id: UUID
    beat_id: Optional[UUID]
    status: VanStockStatus
    invoice_prefix: Optional[str]
    invoice_seq_start: Optional[int]
    invoice_seq_end: Optional[int]
    invoice_seq_current: Optional[int]
    load_date: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class SpotInvoiceCreate(BaseModel):
    """Van sales spot billing — creates order + invoice in one atomic step."""
    van_stock_id: UUID
    customer_id: UUID
    items: list[OrderLineIn]
    payment_method: PaymentMethod = PaymentMethod.cash
    client_uuid: Optional[UUID] = None
    promotion_codes: list[str] = []
    is_intra_state: bool = True


class SpotInvoiceResponse(BaseModel):
    order: OrderResponse
    invoice_number: str
    grand_total: float


class VanSettlementCreate(BaseModel):
    van_stock_id: UUID
    total_cash_collected: float
    total_upi_collected: float
    total_credit_given: float
    physical_returns: dict[str, float] = {}  # sku -> returned_qty
    notes: Optional[str] = None


class VanSettlementResponse(BaseModel):
    id: UUID
    van_stock_id: UUID
    tenant_id: UUID
    driver_id: UUID
    total_invoiced: float
    total_cash_collected: float
    total_upi_collected: float
    total_credit_given: float
    cash_variance: float
    inventory_variance: dict
    status: str
    settled_at: datetime
    model_config = {"from_attributes": True}


@app.post("/van/stocks/", response_model=VanStockResponse, status_code=201, tags=["Van Sales"])
async def create_van_stock(
    data: VanStockCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Start of Day: Load the van with stock and pre-allocate a block of invoice numbers.
    """
    invoice_prefix = data.invoice_prefix or f"VAN-{datetime.utcnow().strftime('%Y%m%d')}"
    seq_start = seq_end = None

    if REDIS_AVAILABLE and settings.REDIS_URL:
        try:
            redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            block_size = 50
            redis_key = f"van:invoice_seq:{current_user.tenant_id}"
            seq_start = await redis_client.incr(redis_key)
            # Atomically reserve a block
            seq_end = seq_start + block_size - 1
            await redis_client.set(redis_key, seq_end)
            await redis_client.aclose()
        except Exception as e:
            logger.warning(f"Redis invoice pre-allocation failed: {e}")
            seq_start = seq_end = None

    van_stock = VanStock(
        tenant_id=current_user.tenant_id,
        driver_id=data.driver_id,
        beat_id=data.beat_id,
        invoice_prefix=invoice_prefix,
        invoice_seq_start=seq_start,
        invoice_seq_end=seq_end,
        invoice_seq_current=seq_start,
    )
    db.add(van_stock)
    await db.flush()

    for item in data.items:
        db.add(VanStockLine(
            van_stock_id=van_stock.id,
            sku=item.sku,
            product_id=item.product_id,
            loaded_quantity=item.loaded_quantity,
        ))

    await db.commit()
    await db.refresh(van_stock)
    return van_stock


@app.get("/van/stocks/", response_model=list[VanStockResponse], tags=["Van Sales"])
async def list_van_stocks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(VanStock)
        .where(VanStock.tenant_id == current_user.tenant_id)
        .offset(skip).limit(limit)
        .order_by(VanStock.created_at.desc())
    )
    return result.scalars().all()


@app.get("/van/stocks/{van_stock_id}", tags=["Van Sales"])
async def get_van_stock(
    van_stock_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(VanStock).where(VanStock.id == van_stock_id, VanStock.tenant_id == current_user.tenant_id)
    )
    van_stock = result.scalar_one_or_none()
    if not van_stock:
        raise HTTPException(status_code=404, detail="Van stock not found")

    lines_result = await db.execute(select(VanStockLine).where(VanStockLine.van_stock_id == van_stock_id))
    lines = lines_result.scalars().all()

    return {
        **VanStockResponse.model_validate(van_stock).model_dump(),
        "items": [VanStockLineResponse.model_validate(l).model_dump() for l in lines],
    }


@app.post("/van/stocks/{van_stock_id}/spot-invoice", tags=["Van Sales"])
async def spot_invoice(
    van_stock_id: UUID,
    data: SpotInvoiceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Van Sales Spot Billing: Create an order + invoice atomically.
    Deducts from van inventory. Uses pre-allocated invoice numbers.
    """
    # Load van stock
    vs_result = await db.execute(
        select(VanStock).where(VanStock.id == van_stock_id, VanStock.tenant_id == current_user.tenant_id)
    )
    van_stock = vs_result.scalar_one_or_none()
    if not van_stock:
        raise HTTPException(status_code=404, detail="Van stock not found")
    if van_stock.status != VanStockStatus.loaded:
        raise HTTPException(status_code=400, detail=f"Van stock is {van_stock.status.value}, not 'loaded'")

    # Check inventory availability
    lines_result = await db.execute(select(VanStockLine).where(VanStockLine.van_stock_id == van_stock_id))
    van_lines = {l.sku: l for l in lines_result.scalars().all()}
    for item in data.items:
        vl = van_lines.get(item.sku)
        available = (vl.loaded_quantity - vl.sold_quantity) if vl else 0
        if available < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient van stock for SKU {item.sku}: available={available}, requested={item.quantity}"
            )

    # Idempotency check
    if data.client_uuid:
        existing = await db.execute(select(Order).where(Order.client_uuid == data.client_uuid))
        existing_order = existing.scalar_one_or_none()
        if existing_order:
            inv_result = await db.execute(select(Invoice).where(Invoice.order_id == existing_order.id))
            inv = inv_result.scalar_one_or_none()
            return SpotInvoiceResponse(
                order=OrderResponse.model_validate(existing_order),
                invoice_number=inv.invoice_number if inv else "N/A",
                grand_total=existing_order.grand_total,
            )

    # Run rule engine + tax calculator
    order_items = [OrderItem(sku=i.sku, quantity=i.quantity, unit_price=i.unit_price) for i in data.items]
    context_obj = OrderContext(
        items=order_items,
        tenant_id=current_user.tenant_id,
        customer_id=str(data.customer_id),
    )
    promo_results = evaluate_promotions([], context_obj)  # No promo codes for spot billing simplicity
    tax_items = [
        {"sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price, "tax_rate_percent": 0.0}
        for i in data.items
    ]
    tax_summary = calculate_taxes(tax_items, is_intra_state=data.is_intra_state)

    # Create order (auto-confirmed for van sales)
    order = Order(
        client_uuid=data.client_uuid,
        tenant_id=current_user.tenant_id,
        customer_id=data.customer_id,
        sales_rep_id=current_user.sub,
        status=OrderStatus.confirmed,
        payment_method=data.payment_method,
        subtotal=tax_summary.subtotal,
        discount_amount=tax_summary.total_discount,
        tax_amount=tax_summary.total_tax,
        grand_total=tax_summary.grand_total,
    )
    db.add(order)
    await db.flush()

    for item in data.items:
        db.add(OrderLine(
            order_id=order.id, sku=item.sku, name=item.name,
            quantity=item.quantity, unit_price=item.unit_price,
            subtotal=item.quantity * item.unit_price,
        ))

    # Generate invoice number from pre-allocated block
    if van_stock.invoice_seq_current and van_stock.invoice_seq_end and van_stock.invoice_seq_current <= van_stock.invoice_seq_end:
        invoice_number = f"{van_stock.invoice_prefix}-{van_stock.invoice_seq_current:04d}"
        van_stock.invoice_seq_current += 1
    else:
        invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"

    invoice = Invoice(
        order_id=order.id,
        tenant_id=current_user.tenant_id,
        invoice_number=invoice_number,
        grand_total=tax_summary.grand_total,
        tax_breakdown={"total_tax": tax_summary.total_tax},
        is_paid=data.payment_method != PaymentMethod.credit,
    )
    db.add(invoice)

    # Update van inventory
    for item in data.items:
        vl = van_lines.get(item.sku)
        if vl:
            vl.sold_quantity += item.quantity

    await db.commit()
    await db.refresh(order)

    return SpotInvoiceResponse(
        order=OrderResponse.model_validate(order),
        invoice_number=invoice_number,
        grand_total=tax_summary.grand_total,
    )


@app.post("/van/settle/", response_model=VanSettlementResponse, status_code=201, tags=["Van Sales"])
async def settle_van(
    data: VanSettlementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    End of Day: Reconcile van inventory and cash.
    Calculates variances and creates a settlement record.
    """
    vs_result = await db.execute(
        select(VanStock).where(VanStock.id == data.van_stock_id, VanStock.tenant_id == current_user.tenant_id)
    )
    van_stock = vs_result.scalar_one_or_none()
    if not van_stock:
        raise HTTPException(status_code=404, detail="Van stock not found")
    if van_stock.status == VanStockStatus.settled:
        raise HTTPException(status_code=400, detail="Van stock already settled")

    # Calculate total invoiced from confirmed orders linked to this van
    # (In full implementation, orders would have van_stock_id FK; here we use driver_id + date)
    from sqlalchemy import cast, Date as SQLDate
    orders_result = await db.execute(
        select(Order).where(
            Order.tenant_id == current_user.tenant_id,
            Order.sales_rep_id == str(van_stock.driver_id),
            Order.status == OrderStatus.confirmed,
            func.date(Order.created_at) == func.date(van_stock.load_date),
        )
    )
    confirmed_orders = orders_result.scalars().all()
    total_invoiced = sum(o.grand_total for o in confirmed_orders)

    # Cash collected
    total_collected = data.total_cash_collected + data.total_upi_collected + data.total_credit_given
    cash_variance = total_invoiced - total_collected

    # Inventory variance per SKU
    lines_result = await db.execute(select(VanStockLine).where(VanStockLine.van_stock_id == data.van_stock_id))
    van_lines = lines_result.scalars().all()
    inventory_variance = {}
    for vl in van_lines:
        physical_returned = data.physical_returns.get(vl.sku, 0.0)
        vl.returned_quantity = physical_returned
        expected_remaining = vl.loaded_quantity - vl.sold_quantity
        actual_remaining = physical_returned
        variance = expected_remaining - actual_remaining
        if abs(variance) > 0.001:
            inventory_variance[vl.sku] = round(variance, 3)

    # Create settlement
    settlement = VanSettlement(
        van_stock_id=data.van_stock_id,
        tenant_id=current_user.tenant_id,
        driver_id=van_stock.driver_id,
        total_invoiced=total_invoiced,
        total_cash_collected=data.total_cash_collected,
        total_upi_collected=data.total_upi_collected,
        total_credit_given=data.total_credit_given,
        cash_variance=round(cash_variance, 2),
        inventory_variance=inventory_variance,
        notes=data.notes,
    )
    db.add(settlement)

    van_stock.status = VanStockStatus.settled
    van_stock.settled_at = datetime.utcnow()

    await db.commit()
    await db.refresh(settlement)
    return settlement


@app.get("/van/settlements/{van_stock_id}", response_model=VanSettlementResponse, tags=["Van Sales"])
async def get_van_settlement(
    van_stock_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    result = await db.execute(
        select(VanSettlement).where(
            VanSettlement.van_stock_id == van_stock_id,
            VanSettlement.tenant_id == current_user.tenant_id,
        )
    )
    settlement = result.scalar_one_or_none()
    if not settlement:
        raise HTTPException(status_code=404, detail="Settlement not found for this van stock")
    return settlement


@app.patch("/van/settlements/{settlement_id}/approve", response_model=VanSettlementResponse, tags=["Van Sales"])
async def approve_van_settlement(
    settlement_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    if "manager" not in current_user.roles and "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Only managers and admins can approve settlements")
    result = await db.execute(select(VanSettlement).where(VanSettlement.id == settlement_id))
    settlement = result.scalar_one_or_none()
    if not settlement:
        raise HTTPException(status_code=404, detail="Settlement not found")
    settlement.status = "approved"
    settlement.approved_by = current_user.sub
    settlement.approved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(settlement)
    return settlement


# ─────────────────────────────────────────────
# SYNC EVENTS (Offline push from Orchestration)
# ─────────────────────────────────────────────

class SyncEventIn(BaseModel):
    event_id: str
    event_type: str
    domain: str
    timestamp: Optional[int] = None
    payload: dict = {}


@app.post("/sync/events", tags=["Sync"])
async def receive_sync_event(
    event: SyncEventIn,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Receives offline domain events from the Orchestration service.
    OrderCreated: re-uses the main create_order logic via direct call.
    SalesReturnCreated: creates a return record idempotently.
    """
    if event.event_type == "OrderCreated":
        payload = event.payload
        try:
            items = [OrderLineIn(**item) for item in payload.get("items", [])]
            order_data = OrderCreate(
                client_uuid=payload.get("client_uuid"),
                customer_id=payload["customer_id"],
                payment_method=payload.get("payment_method"),
                items=items,
                promotion_codes=payload.get("promotion_codes", []),
                client_total=payload.get("client_total"),
                notes=payload.get("notes"),
                is_intra_state=payload.get("is_intra_state", True),
            )
            order = await create_order(order_data, db, current_user)
            return {"event_id": event.event_id, "status": "accepted", "order_id": str(order.id)}
        except Exception as e:
            return {"event_id": event.event_id, "status": "rejected", "reason": str(e)}

    elif event.event_type == "SalesReturnCreated":
        payload = event.payload
        try:
            items = [ReturnLineIn(**item) for item in payload.get("items", [])]
            return_data = SalesReturnCreate(
                client_uuid=payload.get("client_uuid"),
                order_id=payload["order_id"],
                customer_id=payload["customer_id"],
                return_reason=payload.get("return_reason"),
                items=items,
            )
            ret = await create_sales_return(return_data, db, current_user)
            return {"event_id": event.event_id, "status": "accepted", "return_id": str(ret.id)}
        except Exception as e:
            return {"event_id": event.event_id, "status": "rejected", "reason": str(e)}

    return {"event_id": event.event_id, "status": "skipped", "reason": f"unhandled event type: {event.event_type}"}
