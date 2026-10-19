from fastapi import APIRouter, Response, status
from app.database import get_pool
from app.redis_client import get_redis

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check(response: Response):
    """
    Service healthcheck used by Docker Compose and orchestration probes.
    Verifies connectivity to both PostgreSQL and Redis.
    """
    db_ok = False
    redis_ok = False

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
            db_ok = True
    except Exception:
        db_ok = False

    try:
        redis = get_redis()
        pong = await redis.ping()
        redis_ok = bool(pong)
    except Exception:
        redis_ok = False

    is_healthy = db_ok and redis_ok
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected"
    }
