from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "Catalog Service"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    PORT: int = 8002
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/catalog_db"
    REDIS_URL: str = "redis://localhost:6379/1"
    JWT_PUBLIC_KEY_PATH: str = "../../infra/keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    @property
    def jwt_public_key(self) -> str:
        with open(self.JWT_PUBLIC_KEY_PATH) as f:
            return f.read()

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
