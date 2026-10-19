import asyncio
import logging
import math
import time
from typing import Optional, Tuple
import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger("api_gateway.redis")

_redis: Optional[aioredis.Redis] = None
_last_timestamp: float = 0.0
_ts_lock = asyncio.Lock()

async def connect_redis():
    global _redis
    logger.info(f"Connecting to Redis at {settings.REDIS_URL}...")
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5
    )
    # Ping Redis to verify connectivity
    await _redis.ping()
    logger.info("Redis connection established.")

async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        logger.info("Redis connection closed.")

def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis client has not been initialized.")
    return _redis

async def get_unique_monotonic_timestamp() -> float:
    """
    Ensures strictly monotonic floating-point timestamps with microsecond resolution.
    Guarantees unique scores and values for ZADD even under burst concurrency.
    """
    global _last_timestamp
    async with _ts_lock:
        now = time.time()
        if now <= _last_timestamp:
            now = _last_timestamp + 0.000001
        _last_timestamp = now
        return now

async def check_sliding_window_rate_limit(
    api_key_id: int,
    limit_per_minute: int,
    window_seconds: float = 60.0
) -> Tuple[bool, int, int]:
    """
    First-principles Sliding Window Log Rate Limiter using Redis Sorted Sets.
    
    Algorithm:
    1. Timestamp is acquired in seconds (float).
    2. Redis key: rate_limit:{apiKeyId}
    3. Transaction MULTI / EXEC:
       a. ZREMRANGEBYSCORE {key} 0 {currentTime - window}
          (Prunes timestamps older than the sliding window)
       b. ZADD {key} {currentTime} {currentTime}
          (Adds current request timestamp; score and value are the same)
       c. ZCARD {key}
          (Counts total requests within the sliding window)
       d. EXPIRE {key} {window * 2}
          (Ensures cleanup of idle keys)
    4. If count > limit_per_minute:
       Calculate Retry-After based on the oldest element in the window.
       Return (False, retry_after, count).
    5. If count <= limit_per_minute:
       Return (True, 0, count).
    """
    client = get_redis()
    key = f"rate_limit:{api_key_id}"
    current_time = await get_unique_monotonic_timestamp()
    cutoff_time = current_time - window_seconds
    timestamp_str = f"{current_time:.6f}"

    # Transactional execution using MULTI / EXEC
    pipe = client.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, cutoff_time)
    pipe.zadd(key, {timestamp_str: current_time})
    pipe.zcard(key)
    pipe.expire(key, int(window_seconds * 2))
    
    results = await pipe.execute()
    # results[0] -> count of elements pruned
    # results[1] -> count of elements added (1)
    # results[2] -> zcard count
    current_count = results[2]

    if current_count > limit_per_minute:
        # Rate limit exceeded: determine exact time until oldest request rolls out of the window
        oldest_entries = await client.zrange(key, 0, 0, withscores=True)
        if oldest_entries:
            _, oldest_score = oldest_entries[0]
            remaining = (float(oldest_score) + window_seconds) - current_time
            retry_after = max(1, math.ceil(remaining))
        else:
            retry_after = int(window_seconds)

        return False, retry_after, current_count

    return True, 0, current_count
