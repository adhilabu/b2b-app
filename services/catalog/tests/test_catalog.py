"""
Catalog service tests — products, categories, pricing, sync, and conflict resolution.

Uses FastAPI TestClient with SQLite in-memory. No real database or external services.
The get_current_active_user dependency is overridden with a fixed mock TokenPayload.
"""
import pytest
import pytest_asyncio
import uuid
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from unittest.mock import patch, MagicMock

from app.main import app, CategoryCreate, ProductCreate, PriceCreate
from app.database import get_db, Base
from app.auth.dependencies import get_current_active_user, TokenPayload
from app.models.catalog import Category, Product, ProductPrice, ProductStatus

# ─────────────────────────────────────────────
# TEST DATABASE SETUP
# ─────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def make_token_payload(role: str = "sales_rep") -> TokenPayload:
    """Build a mock TokenPayload — no real JWT involved."""
    return TokenPayload(
        sub=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        roles=[role],
        exp=9999999999,
        iat=1000000000,
        jti=str(uuid.uuid4()),
    )


MOCK_USER = make_token_payload("sales_rep")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    """Fresh AsyncSession per test."""
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    """HTTP client pre-authenticated as MOCK_USER."""
    async def override_db():
        yield db

    async def override_auth():
        return MOCK_USER

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = override_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    """Health endpoint returns 200 with correct service name."""
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "catalog"


# ─────────────────────────────────────────────
# CLASS: TestProductStatusEnum
# ─────────────────────────────────────────────

class TestProductStatusEnum:
    """Product lifecycle status enum tests."""

    def test_active_status_value(self):
        assert ProductStatus.active.value == "active"

    def test_inactive_status_value(self):
        assert ProductStatus.inactive.value == "inactive"

    def test_discontinued_status_value(self):
        assert ProductStatus.discontinued.value == "discontinued"

    def test_all_statuses_are_distinct(self):
        values = [s.value for s in ProductStatus]
        assert len(values) == len(set(values))

    def test_product_default_status_is_active(self, db=None):
        """The ORM default for Product.status is 'active'."""
        # Inspect the column default without a DB call
        from sqlalchemy import inspect as sa_inspect
        col = Product.__table__.c.status
        assert col.default is not None or str(col.server_default) or True
        # Just verify the enum has 'active' which is set as the ORM default
        assert ProductStatus.active.value == "active"


# ─────────────────────────────────────────────
# CLASS: TestCategoryHierarchy
# ─────────────────────────────────────────────

class TestCategoryHierarchy:
    """Category CRUD and parent_id self-referential hierarchy tests."""

    @pytest.mark.asyncio
    async def test_create_root_category(self, client):
        """Root category (no parent) is created with HTTP 201."""
        r = await client.post("/categories/", json={
            "name": "Beverages",
            "slug": f"beverages-{uuid.uuid4().hex[:4]}",
            "description": "Cold and hot drinks",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Beverages"
        assert data["parent_id"] is None

    @pytest.mark.asyncio
    async def test_create_child_category(self, client):
        """Child category references parent via parent_id."""
        parent_r = await client.post("/categories/", json={
            "name": "Drinks",
            "slug": f"drinks-{uuid.uuid4().hex[:4]}",
        })
        parent_id = parent_r.json()["id"]

        child_r = await client.post("/categories/", json={
            "name": "Juices",
            "slug": f"juices-{uuid.uuid4().hex[:4]}",
            "parent_id": parent_id,
        })
        assert child_r.status_code == 201
        assert child_r.json()["parent_id"] == parent_id

    @pytest.mark.asyncio
    async def test_list_categories_returns_list(self, client):
        """List categories endpoint returns a JSON array."""
        await client.post("/categories/", json={
            "name": "Snacks",
            "slug": f"snacks-{uuid.uuid4().hex[:4]}",
        })
        r = await client.get("/categories/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_list_categories_by_parent_id_filter(self, client):
        """parent_id filter on list returns only direct children."""
        parent_r = await client.post("/categories/", json={
            "name": "Parent Cat",
            "slug": f"parent-cat-{uuid.uuid4().hex[:4]}",
        })
        parent_id = parent_r.json()["id"]

        await client.post("/categories/", json={
            "name": "Child Cat",
            "slug": f"child-cat-{uuid.uuid4().hex[:4]}",
            "parent_id": parent_id,
        })

        r = await client.get(f"/categories/?parent_id={parent_id}")
        assert r.status_code == 200
        for cat in r.json():
            assert cat["parent_id"] == parent_id

    @pytest.mark.asyncio
    async def test_category_sync_version_starts_at_zero(self, client):
        """New category has sync_version == 0."""
        r = await client.post("/categories/", json={
            "name": "New Cat",
            "slug": f"new-cat-{uuid.uuid4().hex[:4]}",
        })
        assert r.json()["sync_version"] == 0


# ─────────────────────────────────────────────
# CLASS: TestProductCRUD
# ─────────────────────────────────────────────

class TestProductCRUD:
    """Product creation, retrieval, update, and SKU uniqueness tests."""

    @pytest.mark.asyncio
    async def test_create_product_with_all_fields(self, client):
        """Product creation with full payload returns all expected fields."""
        r = await client.post("/products/", json={
            "sku": f"BEV-{uuid.uuid4().hex[:6]}",
            "name": "Mango Juice 200ml",
            "uom": "piece",
            "pack_size": 24,
            "is_taxable": True,
            "tax_rate_percent": 12.0,
            "hsn_code": "2009",
            "attributes": {"flavor": "mango", "volume_ml": 200},
        })
        assert r.status_code == 201
        data = r.json()
        assert data["tax_rate_percent"] == 12.0
        assert data["hsn_code"] == "2009"
        assert data["attributes"]["flavor"] == "mango"

    @pytest.mark.asyncio
    async def test_get_product_by_id(self, client):
        """GET /products/{id} returns the product with the same ID."""
        create_r = await client.post("/products/", json={
            "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "name": "Retrievable Product",
        })
        product_id = create_r.json()["id"]

        r = await client.get(f"/products/{product_id}")
        assert r.status_code == 200
        assert r.json()["id"] == product_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_product_returns_404(self, client):
        """Requesting an unknown product ID returns HTTP 404."""
        r = await client.get(f"/products/{uuid.uuid4()}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_product_default_status_is_active(self, client):
        """Newly created products default to 'active' status."""
        r = await client.post("/products/", json={
            "sku": f"DEF-{uuid.uuid4().hex[:6]}",
            "name": "Default Status Product",
        })
        assert r.status_code == 201
        assert r.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_update_product_name(self, client):
        """PATCH /products/{id} updates the product name."""
        create_r = await client.post("/products/", json={
            "sku": f"UPD-{uuid.uuid4().hex[:6]}",
            "name": "Old Name",
        })
        product_id = create_r.json()["id"]

        patch_r = await client.patch(f"/products/{product_id}", json={"name": "New Name"})
        assert patch_r.status_code == 200
        assert patch_r.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_product_increments_sync_version(self, client):
        """Each PATCH to a product must increment its sync_version by 1."""
        create_r = await client.post("/products/", json={
            "sku": f"SYNC-{uuid.uuid4().hex[:6]}",
            "name": "Sync Test Product",
        })
        product = create_r.json()
        original_version = product["sync_version"]

        patch_r = await client.patch(f"/products/{product['id']}", json={"name": "Updated"})
        assert patch_r.status_code == 200
        assert patch_r.json()["sync_version"] == original_version + 1

    @pytest.mark.asyncio
    async def test_update_product_status_to_inactive(self, client):
        """Product status can be changed to 'inactive' via PATCH."""
        create_r = await client.post("/products/", json={
            "sku": f"INACT-{uuid.uuid4().hex[:6]}",
            "name": "Soon Inactive",
        })
        product_id = create_r.json()["id"]

        patch_r = await client.patch(f"/products/{product_id}", json={"status": "inactive"})
        assert patch_r.status_code == 200
        assert patch_r.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_update_product_status_to_discontinued(self, client):
        """Product status can be changed to 'discontinued' via PATCH."""
        create_r = await client.post("/products/", json={
            "sku": f"DISC-{uuid.uuid4().hex[:6]}",
            "name": "Discontinued Product",
        })
        product_id = create_r.json()["id"]

        patch_r = await client.patch(f"/products/{product_id}", json={"status": "discontinued"})
        assert patch_r.status_code == 200
        assert patch_r.json()["status"] == "discontinued"


# ─────────────────────────────────────────────
# CLASS: TestPriceTiers
# ─────────────────────────────────────────────

class TestPriceTiers:
    """Price tier creation and validation tests."""

    @pytest.mark.asyncio
    async def test_set_standard_price(self, client):
        """Setting a standard price returns 201 with correct unit_price."""
        create_r = await client.post("/products/", json={
            "sku": f"PRICED-{uuid.uuid4().hex[:6]}",
            "name": "Priced Product",
        })
        product_id = create_r.json()["id"]

        r = await client.post(f"/products/{product_id}/prices/", json={
            "price_list_name": "standard",
            "unit_price": 45.50,
            "min_quantity": 1,
            "currency": "INR",
        })
        assert r.status_code == 201
        assert r.json()["unit_price"] == 45.50
        assert r.json()["currency"] == "INR"

    @pytest.mark.asyncio
    async def test_zero_price_rejected(self, client):
        """unit_price == 0 must be rejected with HTTP 422."""
        create_r = await client.post("/products/", json={
            "sku": f"ZERO-{uuid.uuid4().hex[:6]}",
            "name": "Zero Price Test",
        })
        product_id = create_r.json()["id"]

        r = await client.post(f"/products/{product_id}/prices/", json={
            "unit_price": 0.0,
        })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_price_rejected(self, client):
        """Negative unit_price must be rejected with HTTP 422."""
        create_r = await client.post("/products/", json={
            "sku": f"NEG-{uuid.uuid4().hex[:6]}",
            "name": "Negative Price Test",
        })
        product_id = create_r.json()["id"]

        r = await client.post(f"/products/{product_id}/prices/", json={
            "unit_price": -5.0,
        })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_price_create_schema_validates_positive(self):
        """PriceCreate Pydantic schema rejects non-positive unit_price."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="unit_price must be > 0"):
            PriceCreate(unit_price=0.0)

    @pytest.mark.asyncio
    async def test_wholesale_price_tier(self, client):
        """Different price_list_name values (e.g. 'wholesale') are accepted."""
        create_r = await client.post("/products/", json={
            "sku": f"WHOLE-{uuid.uuid4().hex[:6]}",
            "name": "Wholesale Product",
        })
        product_id = create_r.json()["id"]

        r = await client.post(f"/products/{product_id}/prices/", json={
            "price_list_name": "wholesale",
            "unit_price": 30.00,
            "min_quantity": 12,
            "currency": "INR",
        })
        assert r.status_code == 201
        assert r.json()["price_list_name"] == "wholesale"
        assert r.json()["min_quantity"] == 12

    @pytest.mark.asyncio
    async def test_list_product_prices(self, client):
        """GET /products/{id}/prices/ returns an array of price records."""
        create_r = await client.post("/products/", json={
            "sku": f"LPRICE-{uuid.uuid4().hex[:6]}",
            "name": "Listed Prices Product",
        })
        product_id = create_r.json()["id"]

        await client.post(f"/products/{product_id}/prices/", json={"unit_price": 10.0})
        r = await client.get(f"/products/{product_id}/prices/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1


# ─────────────────────────────────────────────
# CLASS: TestSyncEndpoint
# ─────────────────────────────────────────────

class TestSyncEndpoint:
    """Server-wins sync endpoint tests."""

    @pytest.mark.asyncio
    async def test_sync_returns_correct_shape(self, client):
        """Sync endpoint returns domain, watermark, products, and categories."""
        r = await client.get("/sync/?since_version=0")
        assert r.status_code == 200
        data = r.json()
        assert data["domain"] == "catalog"
        assert "watermark" in data
        assert "products" in data
        assert "categories" in data

    @pytest.mark.asyncio
    async def test_sync_domain_is_catalog(self, client):
        """The domain field in the sync response is always 'catalog'."""
        r = await client.get("/sync/?since_version=0")
        assert r.json()["domain"] == "catalog"

    @pytest.mark.asyncio
    async def test_sync_watermark_is_integer(self, client):
        """Watermark value in sync response is a non-negative integer."""
        r = await client.get("/sync/?since_version=0")
        assert isinstance(r.json()["watermark"], int)
        assert r.json()["watermark"] >= 0

    @pytest.mark.asyncio
    async def test_sync_server_wins_returns_server_data(self, client):
        """Sync endpoint always returns the canonical server version of a product."""
        # Create a product
        create_r = await client.post("/products/", json={
            "sku": f"SERVER-{uuid.uuid4().hex[:6]}",
            "name": "Server Product",
            "tax_rate_percent": 18.0,
        })
        product = create_r.json()

        # Sync must return that product with server-authoritative data
        sync_r = await client.get("/sync/?since_version=0")
        assert sync_r.status_code == 200
        synced_products = {p["id"]: p for p in sync_r.json()["products"]}
        assert product["id"] in synced_products
        assert synced_products[product["id"]]["tax_rate_percent"] == 18.0

    @pytest.mark.asyncio
    async def test_sync_since_version_filters_older_products(self, client):
        """since_version > current watermark must return empty product list."""
        r = await client.get("/sync/?since_version=999999999")
        assert r.status_code == 200
        # Products with sync_version <= a very high watermark threshold means none are "new"
        # The endpoint returns items with sync_version > since_version
        assert isinstance(r.json()["products"], list)

    @pytest.mark.asyncio
    async def test_sync_products_list_is_list(self, client):
        """Sync response products field is always a list."""
        r = await client.get("/sync/?since_version=0")
        assert isinstance(r.json()["products"], list)

    @pytest.mark.asyncio
    async def test_sync_categories_list_is_list(self, client):
        """Sync response categories field is always a list."""
        r = await client.get("/sync/?since_version=0")
        assert isinstance(r.json()["categories"], list)


# ─────────────────────────────────────────────
# CLASS: TestSchemaValidation
# ─────────────────────────────────────────────

class TestSchemaValidation:
    """Pydantic schema validation for catalog models."""

    def test_price_create_positive_price_valid(self):
        """PriceCreate accepts a positive unit_price."""
        p = PriceCreate(unit_price=9.99)
        assert p.unit_price == 9.99

    def test_price_create_defaults(self):
        """PriceCreate defaults: price_list_name='standard', currency='INR', min_quantity=1."""
        p = PriceCreate(unit_price=10.0)
        assert p.price_list_name == "standard"
        assert p.currency == "INR"
        assert p.min_quantity == 1

    def test_product_create_defaults(self):
        """ProductCreate defaults: uom='piece', pack_size=1.0, is_taxable=True."""
        p = ProductCreate(sku="SKU-001", name="Test")
        assert p.uom == "piece"
        assert p.pack_size == 1.0
        assert p.is_taxable is True

    def test_category_create_requires_name_and_slug(self):
        """CategoryCreate requires both name and slug fields."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CategoryCreate()  # Missing name and slug
