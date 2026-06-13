import uuid
import enum
from sqlalchemy import (
    Column, String, Boolean, DateTime, Enum, ForeignKey,
    Float, Integer, Text, func, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class ProductStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    discontinued = "discontinued"


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    sync_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship("Category", back_populates="parent")
    products = relationship("Product", back_populates="category")

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_category_tenant_slug"),
        Index("ix_categories_tenant_parent", "tenant_id", "parent_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)

    sku = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    barcode = Column(String(100), nullable=True, index=True)

    # Unit info
    uom = Column(String(50), default="piece", nullable=False)  # Unit of measurement
    pack_size = Column(Float, default=1.0, nullable=False)     # Units per carton
    weight_kg = Column(Float, nullable=True)

    # Status
    status = Column(Enum(ProductStatus), default=ProductStatus.active, nullable=False)
    is_taxable = Column(Boolean, default=True)
    tax_rate_percent = Column(Float, default=0.0)
    hsn_code = Column(String(20), nullable=True)  # India GST HSN code

    # Media
    image_urls = Column(JSONB, default=[], nullable=False)
    attributes = Column(JSONB, default={}, nullable=False)
    # e.g., {"color": "red", "flavor": "mango"}

    sync_version = Column(Integer, default=0, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    category = relationship("Category", back_populates="products")
    prices = relationship("ProductPrice", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
        Index("ix_products_tenant_status", "tenant_id", "status"),
    )


class ProductPrice(Base):
    """Base pricing tier — server-wins model, offline clients cannot mutate."""
    __tablename__ = "product_prices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    price_list_name = Column(String(100), default="standard", nullable=False)
    # e.g., "standard", "wholesale", "vip"
    unit_price = Column(Float, nullable=False)
    min_quantity = Column(Integer, default=1, nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    is_active = Column(Boolean, default=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "price_list_name", "min_quantity",
                         name="uq_price_product_list_qty"),
    )
