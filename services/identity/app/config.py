from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Identity Service"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    PORT: int = 8001

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://b2b_admin:change_me@localhost:5432/identity_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT (RS256)
    JWT_PRIVATE_KEY_PATH: str = "../../infra/keys/private.pem"
    JWT_PUBLIC_KEY_PATH: str = "../../infra/keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    @property
    def jwt_private_key(self) -> str:
        with open(self.JWT_PRIVATE_KEY_PATH, "r") as f:
            return f.read()

    @property
    def jwt_public_key(self) -> str:
        with open(self.JWT_PUBLIC_KEY_PATH, "r") as f:
            return f.read()

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
