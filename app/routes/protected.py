import logging
from typing import Optional
from fastapi import APIRouter, Header, Response, status
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import authenticate_key_hash, insert_audit_log
from app.redis_client import check_sliding_window_rate_limit
from app.security import hash_api_key

logger = logging.getLogger("api_gateway.protected")

router = APIRouter(tags=["Protected Resources"])

@router.get(
    "/api/protected",
    summary="Protected resource requiring valid API key and rate limit enforcement"
)
async def get_protected_resource(
    authorization: Optional[str] = Header(None)
):
    """
    Protected Endpoint:
    1. Extracts 'Authorization: Bearer <key>'.
    2. Rejects invalid / missing / expired / revoked keys with 401 Unauthorized.
    3. Evaluates Redis Sorted Set sliding-window rate limiter.
    4. Records authenticated request in audit_logs table (both 200 and 429).
    5. Returns 429 with 'Retry-After' header if rate limit is exceeded.
    6. Returns 200 OK if authenticated and under rate limit.
    """
    # 1. Validate Authorization header format
    if not authorization or not authorization.startswith("Bearer "):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or malformed Authorization header. Expected 'Bearer <key>'."}
        )

    raw_token = authorization[7:].strip()
    if not raw_token:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Bearer token is empty."}
        )

    # 2. Hash provided key and look up in database
    token_hash = hash_api_key(raw_token)
    key_record = await authenticate_key_hash(token_hash)

    if not key_record:
        # Invalid key, inactive, or rotation grace period expired
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid, revoked, or expired API key."}
        )

    api_key_id = key_record["id"]
    tenant_id = key_record["tenant_id"]
    rate_limit = key_record["rate_limit_per_minute"]

    # 3. Check sliding-window rate limit
    allowed, retry_after, current_count = await check_sliding_window_rate_limit(
        api_key_id=api_key_id,
        limit_per_minute=rate_limit,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
    )

    if not allowed:
        # Rate limit exceeded -> record 429 in audit log
        await insert_audit_log(
            api_key_id=api_key_id,
            endpoint="/api/protected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": f"Rate limit of {rate_limit} requests per minute exceeded.",
                "retryAfter": retry_after,
                "currentRequests": current_count
            },
            headers={"Retry-After": str(retry_after)}
        )

    # Request allowed -> record 200 in audit log
    await insert_audit_log(
        api_key_id=api_key_id,
        endpoint="/api/protected",
        status_code=status.HTTP_200_OK
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Access granted to protected resource",
            "tenantId": tenant_id,
            "apiKeyId": api_key_id,
            "quotaUsed": current_count,
            "rateLimitPerMinute": rate_limit
        }
    )
