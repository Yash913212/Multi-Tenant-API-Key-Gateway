import os
from pydantic import BaseModel

class Settings(BaseModel):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgrespassword@localhost:5432/api_gateway")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    DEFAULT_RATE_LIMIT: int = int(os.getenv("DEFAULT_RATE_LIMIT", "100"))
    DEFAULT_KEY_PREFIX: str = os.getenv("DEFAULT_KEY_PREFIX", "sk_live_")
    ROTATION_GRACE_PERIOD_SECONDS: int = int(os.getenv("ROTATION_GRACE_PERIOD_SECONDS", "60"))
    RATE_LIMIT_WINDOW_SECONDS: float = float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60.0"))

settings = Settings()
