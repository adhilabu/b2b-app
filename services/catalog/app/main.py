"""
Catalog Service — FastAPI Application
Product Information Management (PIM): SKUs, Categories, Pricing.
Server-Wins conflict resolution — offline clients cannot mutate catalog data.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, field_validator
from datetime import datetime

from app.config import get_settings
from app.database import engine, Base, get_db
from app.models.catalog import Category, Product, ProductPrice, ProductStatus
from app.auth.dependencies import get_current_active_user, TokenPayload

settings = get_settings()
logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Catalog DB tables ready")
    yield
    await engine.dispose()


app = FastAPI(
    title="Catalog Service",
    description="Product SKUs, hierarchical categories, and base pricing (PIM). Server-Wins conflict model.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# SCHEMAS (inline for service simplicity)
# ─────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    sort_order: int = 0
    image_url: Optional[str] = None


class CategoryResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    description: Optional[str]
    parent_id: Optional[UUID]
    sort_order: int
    is_active: bool
    sync_version: int
    created_at: datetime
    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    sku: str
    name: str
    category_id: Optional[UUID] = None
    description: Optional[str] = None
    barcode: Optional[str] = None
    uom: str = "piece"
    pack_size: float = 1.0
    weight_kg: Optional[float] = None
    is_taxable: bool = True
    tax_rate_percent: float = 0.0
    hsn_code: Optional[str] = None
    image_urls: list[str] = []
    attributes: dict = {}


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ProductStatus] = None
    is_taxable: Optional[bool] = None
    tax_rate_percent: Optional[float] = None
    attributes: Optional[dict] = None


class ProductResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    sku: str
    name: str
    category_id: Optional[UUID]
    uom: str
    pack_size: float
    status: ProductStatus
    is_taxable: bool
    tax_rate_percent: float
    hsn_code: Optional[str]
    image_urls: list
    attributes: dict
    sync_version: int
    created_at: datetime
    model_config = {"from_attributes": True}


class PriceCreate(BaseModel):
    price_list_name: str = "standard"
    unit_price: float
    min_quantity: int = 1
    currency: str = "INR"
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    @field_validator("unit_price")
    @classmethod
    def price_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("unit_price must be > 0")
        return v


class PriceResponse(BaseModel):
    id: UUID
    product_id: UUID
    price_list_name: str
    unit_price: float
    min_quantity: int
    currency: str
    is_active: bool
    model_config = {"from_attributes": True}


class SyncResponse(BaseModel):
    domain: str = "catalog"
    watermark: int
    products: list[ProductResponse] = []
    categories: list[CategoryResponse] = []


# ─────────────────────────────────────────────
# CATEGORY ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/categories/", response_model=CategoryResponse, status_code=201, tags=["Categories"])
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    cat = Category(
        tenant_id=current_user.tenant_id,
        name=data.name,
        slug=data.slug,
        description=data.description,
        parent_id=data.parent_id,
        sort_order=data.sort_order,
        image_url=data.image_url,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@app.get("/categories/", response_model=list[CategoryResponse], tags=["Categories"])
async def list_categories(
    parent_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    query = select(Category).where(
        Category.tenant_id == current_user.tenant_id,
        Category.is_active == True,
    )
    if parent_id:
        query = query.where(Category.parent_id == parent_id)
    result = await db.execute(query.order_by(Category.sort_order))
    return result.scalars().all()


# ─────────────────────────────────────────────
# PRODUCT ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/products/", response_model=ProductResponse, status_code=201, tags=["Products"])
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    product = Product(
        tenant_id=current_user.tenant_id,
        sku=data.sku,
        name=data.name,
        category_id=data.category_id,
        description=data.description,
        barcode=data.barcode,
        uom=data.uom,
        pack_size=data.pack_size,
        weight_kg=data.weight_kg,
        is_taxable=data.is_taxable,
        tax_rate_percent=data.tax_rate_percent,
        hsn_code=data.hsn_code,
        image_urls=data.image_urls,
        attributes=data.attributes,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@app.get("/products/", response_model=list[ProductResponse], tags=["Products"])
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    category_id: Optional[UUID] = Query(None),
    status: Optional[ProductStatus] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    query = select(Product).where(Product.tenant_id == current_user.tenant_id)
    if category_id:
        query = query.where(Product.category_id == category_id)
    if status:
        query = query.where(Product.status == status)
    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


@app.get("/products/{product_id}", response_model=ProductResponse, tags=["Products"])
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == current_user.tenant_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.patch("/products/{product_id}", response_model=ProductResponse, tags=["Products"])
async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == current_user.tenant_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    product.sync_version += 1
    await db.commit()
    await db.refresh(product)
    return product


# ─────────────────────────────────────────────
# PRICING ENDPOINTS (Server-Wins)
# ─────────────────────────────────────────────

@app.post("/products/{product_id}/prices/", response_model=PriceResponse, status_code=201, tags=["Pricing"])
async def set_price(
    product_id: UUID,
    data: PriceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    """Set or update pricing for a product. Server-Wins: only server-side changes accepted."""
    price = ProductPrice(
        product_id=product_id,
        tenant_id=current_user.tenant_id,
        price_list_name=data.price_list_name,
        unit_price=data.unit_price,
        min_quantity=data.min_quantity,
        currency=data.currency,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )
    db.add(price)
    await db.commit()
    await db.refresh(price)
    return price


@app.get("/products/{product_id}/prices/", response_model=list[PriceResponse], tags=["Pricing"])
async def get_prices(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    result = await db.execute(
        select(ProductPrice).where(
            ProductPrice.product_id == product_id,
            ProductPrice.tenant_id == current_user.tenant_id,
            ProductPrice.is_active == True,
        )
    )
    return result.scalars().all()


# ─────────────────────────────────────────────
# SYNC ENDPOINT
# ─────────────────────────────────────────────

@app.get("/sync/", response_model=SyncResponse, tags=["Sync"])
async def sync_catalog(
    since_version: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_active_user),
):
    """Returns all catalog changes since the given watermark for offline sync."""
    products_result = await db.execute(
        select(Product).where(
            Product.tenant_id == current_user.tenant_id,
            Product.sync_version > since_version,
        )
    )
    products = products_result.scalars().all()

    categories_result = await db.execute(
        select(Category).where(
            Category.tenant_id == current_user.tenant_id,
            Category.sync_version > since_version,
        )
    )
    categories = categories_result.scalars().all()

    max_version = max(
        [p.sync_version for p in products] + [c.sync_version for c in categories] + [since_version]
    )

    return SyncResponse(
        watermark=max_version,
        products=products,
        categories=categories,
    )


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "catalog"}
