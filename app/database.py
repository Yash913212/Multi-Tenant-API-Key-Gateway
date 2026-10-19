import logging
from typing import Optional, List, Dict, Any
import asyncpg
from app.config import settings

logger = logging.getLogger("api_gateway.database")

_pool: Optional[asyncpg.Pool] = None

async def connect_db():
    global _pool
    logger.info(f"Connecting to PostgreSQL at {settings.DATABASE_URL.split('@')[-1]}...")
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=20,
        command_timeout=60,
    )
    # Ensure tables exist (idempotent init)
    await init_db()
    logger.info("PostgreSQL connection pool established.")

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL connection pool closed.")

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized.")
    return _pool

async def init_db():
    """
    Creates tables and indices idempotently if not already present.
    Ensures baseline tenants exist for immediate testing.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                key_hash VARCHAR(255) NOT NULL UNIQUE,
                key_prefix VARCHAR(10) NOT NULL,
                last_four VARCHAR(4) NOT NULL,
                rate_limit_per_minute INTEGER NOT NULL DEFAULT 100,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                expires_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                api_key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                endpoint VARCHAR(255) NOT NULL,
                status_code INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
            CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON api_keys(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_api_keys_is_active_expires ON api_keys(is_active, expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_api_key_id ON audit_logs(api_key_id);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_endpoint ON audit_logs(endpoint);

            INSERT INTO tenants (id, name, created_at)
            VALUES 
                (1, 'Acme Corporation', NOW()),
                (2, 'Globex Industries', NOW())
            ON CONFLICT (id) DO NOTHING;

            SELECT setval('tenants_id_seq', (SELECT GREATEST(MAX(id), 1) FROM tenants));
        """)

# --- Database Helper Queries ---

async def get_tenants() -> List[Dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, created_at FROM tenants ORDER BY id ASC")
        return [dict(r) for r in rows]

async def get_tenant_by_id(tenant_id: int) -> Optional[Dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name, created_at FROM tenants WHERE id = $1", tenant_id)
        return dict(row) if row else None

async def insert_api_key(
    tenant_id: int,
    key_hash: str,
    key_prefix: str,
    last_four: str,
    rate_limit_per_minute: int
) -> Dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO api_keys (tenant_id, key_hash, key_prefix, last_four, rate_limit_per_minute, is_active)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            RETURNING id, tenant_id, last_four, rate_limit_per_minute, is_active, created_at
        """, tenant_id, key_hash, key_prefix, last_four, rate_limit_per_minute)
        return dict(row)

async def get_keys_for_tenant(tenant_id: int) -> List[Dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, tenant_id, key_prefix, last_four, rate_limit_per_minute, is_active, expires_at, created_at
            FROM api_keys
            WHERE tenant_id = $1
            ORDER BY created_at DESC, id DESC
        """, tenant_id)
        return [dict(r) for r in rows]

async def get_key_by_id(key_id: int) -> Optional[Dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, tenant_id, key_hash, key_prefix, last_four, rate_limit_per_minute, is_active, expires_at, created_at
            FROM api_keys
            WHERE id = $1
        """, key_id)
        return dict(row) if row else None

async def authenticate_key_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    """
    Finds an active key matching the hash.
    A key is valid if is_active is TRUE AND (expires_at IS NULL OR expires_at > NOW()).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, tenant_id, rate_limit_per_minute, is_active, expires_at
            FROM api_keys
            WHERE key_hash = $1
              AND is_active = TRUE
              AND (expires_at IS NULL OR expires_at > NOW())
        """, key_hash)
        return dict(row) if row else None

async def revoke_api_key(key_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE api_keys
            SET is_active = FALSE
            WHERE id = $1
        """, key_id)
        # result is e.g. "UPDATE 1"
        return result.endswith("1")

async def rotate_api_key(
    old_key_id: int,
    tenant_id: int,
    rate_limit_per_minute: int,
    new_key_hash: str,
    new_key_prefix: str,
    new_last_four: str,
    grace_period_seconds: int = 60
) -> Dict[str, Any]:
    """
    Rotates a key transactionally:
    1. Sets old key expires_at to grace_period_seconds into the future.
    2. Inserts new active key for the same tenant.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Set grace period on old key
            await conn.execute("""
                UPDATE api_keys
                SET expires_at = NOW() + ($1 || ' seconds')::INTERVAL
                WHERE id = $2
            """, str(grace_period_seconds), old_key_id)

            # Insert new key
            new_row = await conn.fetchrow("""
                INSERT INTO api_keys (tenant_id, key_hash, key_prefix, last_four, rate_limit_per_minute, is_active)
                VALUES ($1, $2, $3, $4, $5, TRUE)
                RETURNING id, tenant_id, last_four, rate_limit_per_minute, is_active, created_at
            """, tenant_id, new_key_hash, new_key_prefix, new_last_four, rate_limit_per_minute)
            return dict(new_row)

async def insert_audit_log(api_key_id: int, endpoint: str, status_code: int) -> Dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO audit_logs (api_key_id, endpoint, status_code, timestamp)
            VALUES ($1, $2, $3, NOW())
            RETURNING id, api_key_id, endpoint, status_code, timestamp
        """, api_key_id, endpoint, status_code)
        return dict(row)

async def get_audit_logs(
    tenant_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if tenant_id is not None:
            total_count = await conn.fetchval("""
                SELECT COUNT(*) FROM audit_logs a
                JOIN api_keys k ON a.api_key_id = k.id
                WHERE k.tenant_id = $1
            """, tenant_id)
            rows = await conn.fetch("""
                SELECT 
                    a.id,
                    a.api_key_id,
                    k.key_prefix,
                    k.last_four,
                    a.endpoint,
                    a.status_code,
                    a.timestamp
                FROM audit_logs a
                JOIN api_keys k ON a.api_key_id = k.id
                WHERE k.tenant_id = $1
                ORDER BY a.timestamp DESC, a.id DESC
                LIMIT $2 OFFSET $3
            """, tenant_id, limit, offset)
        else:
            total_count = await conn.fetchval("SELECT COUNT(*) FROM audit_logs")
            rows = await conn.fetch("""
                SELECT 
                    a.id,
                    a.api_key_id,
                    k.key_prefix,
                    k.last_four,
                    a.endpoint,
                    a.status_code,
                    a.timestamp
                FROM audit_logs a
                JOIN api_keys k ON a.api_key_id = k.id
                ORDER BY a.timestamp DESC, a.id DESC
                LIMIT $1 OFFSET $2
            """, limit, offset)

        items = []
        for r in rows:
            masked = f"{r['key_prefix']}...{r['last_four']}"
            items.append({
                "id": r["id"],
                "apiKeyId": r["api_key_id"],
                "maskedKey": masked,
                "endpoint": r["endpoint"],
                "statusCode": r["status_code"],
                "timestamp": r["timestamp"].isoformat() + "Z" if r["timestamp"] else None
            })

        return {
            "total": total_count,
            "items": items,
            "limit": limit,
            "offset": offset
        }

async def get_analytics_summary(tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Returns metrics and hourly buckets for Chart.js.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        base_where = "WHERE k.tenant_id = $1" if tenant_id is not None else ""
        args = [tenant_id] if tenant_id is not None else []

        # Totals
        query_totals = f"""
            SELECT 
                COUNT(*) as total_requests,
                COUNT(CASE WHEN a.status_code = 200 THEN 1 END) as success_requests,
                COUNT(CASE WHEN a.status_code = 429 THEN 1 END) as rate_limited_requests
            FROM audit_logs a
            JOIN api_keys k ON a.api_key_id = k.id
            {base_where}
        """
        totals_row = await conn.fetchrow(query_totals, *args)

        # Active keys count
        keys_where = "WHERE tenant_id = $1 AND is_active = TRUE" if tenant_id is not None else "WHERE is_active = TRUE"
        active_keys_count = await conn.fetchval(f"SELECT COUNT(*) FROM api_keys {keys_where}", *args)

        # 5-minute time series buckets over the past hour
        query_series = f"""
            SELECT 
                to_char(date_trunc('minute', a.timestamp) - (EXTRACT(minute FROM a.timestamp)::int % 5) * INTERVAL '1 minute', 'HH24:MI') as time_bucket,
                COUNT(CASE WHEN a.status_code = 200 THEN 1 END) as success_count,
                COUNT(CASE WHEN a.status_code = 429 THEN 1 END) as rate_limited_count
            FROM audit_logs a
            JOIN api_keys k ON a.api_key_id = k.id
            {base_where} {"AND" if base_where else "WHERE"} a.timestamp >= NOW() - INTERVAL '1 hour'
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """
        series_rows = await conn.fetch(query_series, *args)

        return {
            "totalRequests": totals_row["total_requests"] if totals_row else 0,
            "successRequests": totals_row["success_requests"] if totals_row else 0,
            "rateLimitedRequests": totals_row["rate_limited_requests"] if totals_row else 0,
            "activeKeysCount": active_keys_count or 0,
            "timeline": [
                {
                    "bucket": r["time_bucket"],
                    "success": r["success_count"],
                    "rateLimited": r["rate_limited_count"]
                }
                for r in series_rows
            ]
        }
