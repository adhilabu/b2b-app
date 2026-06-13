"""Catalog service tests — products, categories, pricing, sync."""
import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from unittest.mock import patch, MagicMock

from app.main import app
from app.database import get_db, Base
from app.auth.dependencies import get_current_active_user, TokenPayload

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def make_token_payload(role="sales_rep") -> TokenPayload:
    return TokenPayload(
        sub=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        roles=[role],
        exp=9999999999,
        iat=1000000000,
        jti=str(uuid.uuid4()),
    )


MOCK_USER = make_token_payload()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    async def override_db():
        yield db

    async def override_auth():
        return MOCK_USER

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = override_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "catalog"


@pytest.mark.asyncio
async def test_create_category(client):
    r = await client.post("/categories/", json={
        "name": "Beverages",
        "slug": "beverages",
        "description": "Cold and hot drinks",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Beverages"
    assert data["slug"] == "beverages"
    return data["id"]


@pytest.mark.asyncio
async def test_list_categories(client):
    await client.post("/categories/", json={"name": "Snacks", "slug": "snacks"})
    r = await client.get("/categories/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_create_product(client):
    r = await client.post("/products/", json={
        "sku": "BEV-001",
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
    assert data["sku"] == "BEV-001"
    assert data["tax_rate_percent"] == 12.0
    return data["id"]


@pytest.mark.asyncio
async def test_get_product(client):
    create_r = await client.post("/products/", json={
        "sku": f"SKU-{uuid.uuid4().hex[:6]}",
        "name": "Test Product",
    })
    product_id = create_r.json()["id"]

    r = await client.get(f"/products/{product_id}")
    assert r.status_code == 200
    assert r.json()["id"] == product_id


@pytest.mark.asyncio
async def test_product_not_found(client):
    r = await client.get(f"/products/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_set_price(client):
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


@pytest.mark.asyncio
async def test_price_must_be_positive(client):
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
async def test_sync_returns_watermark(client):
    r = await client.get("/sync/?since_version=0")
    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "catalog"
    assert "watermark" in data
    assert "products" in data
    assert "categories" in data


@pytest.mark.asyncio
async def test_update_product_increments_sync_version(client):
    create_r = await client.post("/products/", json={
        "sku": f"SYNC-{uuid.uuid4().hex[:6]}",
        "name": "Sync Test Product",
    })
    product = create_r.json()
    original_version = product["sync_version"]

    patch_r = await client.patch(f"/products/{product['id']}", json={"name": "Updated Name"})
    assert patch_r.status_code == 200
    assert patch_r.json()["sync_version"] == original_version + 1
