"""
Identity Service — FastAPI Application Entry Point
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.routers import auth, users, customers, tenants

settings = get_settings()
logging.basicConfig(level=settings.LOG_LEVEL if hasattr(settings, 'LOG_LEVEL') else "INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables. Shutdown: dispose engine."""
    logger.info("🚀 Identity Service starting up...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Database tables ready")
    yield
    logger.info("🛑 Identity Service shutting down...")
    await engine.dispose()


app = FastAPI(
    title="Identity Service",
    description=(
        "User authentication, authorization, tenant management, and customer outlet registry. "
        "Issues RS256 JWT tokens consumed by all downstream microservices."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(customers.router)
app.include_router(tenants.router)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "identity"}
