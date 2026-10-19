"""
Comprehensive Test Suite for Multi-Tenant API Key Gateway.
Verifies all Core Requirements 1 through 9.
"""

import hashlib
import os
import time
import pytest
import httpx
import asyncpg
import redis.asyncio as aioredis

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
DB_URL = os.getenv("DATABASE_URL", os.getenv("TEST_DB_URL", "postgresql://postgres:postgrespassword@localhost:5432/api_gateway"))
REDIS_URL = os.getenv("REDIS_URL", os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0"))

@pytest.fixture
async def http_client():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        yield client

@pytest.fixture
async def db_conn():
    conn = await asyncpg.connect(DB_URL)
    try:
        yield conn
    finally:
        await conn.close()

@pytest.fixture
async def redis_conn():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()

# --- Requirement 1 & Health: Services are up and healthy ---
@pytest.mark.asyncio
async def test_req1_health_check(http_client):
    res = await http_client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["redis"] == "connected"

# --- Requirement 2: Database Schema & Seed Data ---
@pytest.mark.asyncio
async def test_req2_database_schema_and_seed(db_conn):
    # Verify tenants table exists and contains seeded tenant
    tenants = await db_conn.fetch("SELECT id, name FROM tenants ORDER BY id ASC")
    assert len(tenants) >= 1
    assert tenants[0]["id"] == 1
    assert tenants[0]["name"] == "Acme Corporation"

    # Verify api_keys columns
    cols_api_keys = await db_conn.fetch("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'api_keys'
    """)
    col_names = {c["column_name"] for c in cols_api_keys}
    expected_cols = {"id", "tenant_id", "key_hash", "key_prefix", "last_four", "rate_limit_per_minute", "is_active", "expires_at", "created_at"}
    assert expected_cols.issubset(col_names)

    # Verify audit_logs columns
    cols_audit = await db_conn.fetch("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'audit_logs'
    """)
    audit_col_names = {c["column_name"] for c in cols_audit}
    expected_audit_cols = {"id", "api_key_id", "endpoint", "status_code", "timestamp"}
    assert expected_audit_cols.issubset(audit_col_names)

# --- Requirement 3: API Key Issuance and SHA-256 Hashing ---
@pytest.mark.asyncio
async def test_req3_key_issuance_and_hashing(http_client, db_conn):
    tenant_id = 1
    payload = {"rateLimitPerMinute": 100}
    res = await http_client.post(f"/api/tenants/{tenant_id}/keys", json=payload)
    assert res.status_code == 201

    data = res.json()
    assert "apiKey" in data
    assert "keyRecord" in data
    assert data["apiKey"].startswith("sk_live_")
    
    key_record = data["keyRecord"]
    key_id = key_record["id"]
    last_four = key_record["lastFour"]
    assert key_record["rateLimitPerMinute"] == 100
    assert last_four == data["apiKey"][-4:]

    # Query database to confirm plaintext key is NEVER stored, but SHA-256 hash is
    db_row = await db_conn.fetchrow("SELECT key_hash, last_four, rate_limit_per_minute FROM api_keys WHERE id = $1", key_id)
    assert db_row is not None
    assert db_row["key_hash"] != data["apiKey"]
    expected_hash = hashlib.sha256(data["apiKey"].encode('utf-8')).hexdigest()
    assert db_row["key_hash"] == expected_hash
    assert db_row["rate_limit_per_minute"] == 100

# --- Requirement 4: List API Keys with Masking ---
@pytest.mark.asyncio
async def test_req4_list_api_keys_masked(http_client):
    tenant_id = 1
    # Issue a key first
    issue_res = await http_client.post(f"/api/tenants/{tenant_id}/keys", json={"rateLimitPerMinute": 50})
    assert issue_res.status_code == 201
    issued_data = issue_res.json()
    issued_id = issued_data["keyRecord"]["id"]
    issued_plaintext = issued_data["apiKey"]

    # List keys
    list_res = await http_client.get(f"/api/tenants/{tenant_id}/keys")
    assert list_res.status_code == 200
    keys_list = list_res.json()
    assert isinstance(keys_list, list)

    target_key = next((k for k in keys_list if k["id"] == issued_id), None)
    assert target_key is not None
    assert "maskedKey" in target_key
    assert "createdAt" in target_key
    assert target_key["isActive"] is True
    # Masked key must show prefix and last four, but not full key
    assert target_key["maskedKey"].startswith("sk_live_...")
    assert target_key["maskedKey"].endswith(issued_plaintext[-4:])
    assert target_key["maskedKey"] != issued_plaintext

# --- Requirement 5: Protected Endpoint Authentication ---
@pytest.mark.asyncio
async def test_req5_protected_endpoint_authentication(http_client):
    # 1. Issue a valid key
    issue_res = await http_client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 100})
    api_key = issue_res.json()["apiKey"]

    # 2. Success Case with Bearer header
    res_valid = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {api_key}"})
    assert res_valid.status_code == 200
    assert res_valid.json()["message"] == "Access granted to protected resource"

    # 3. Failure: Invalid key
    res_invalid = await http_client.get("/api/protected", headers={"Authorization": "Bearer sk_live_invalidKey123456789"})
    assert res_invalid.status_code == 401

    # 4. Failure: Missing header
    res_missing = await http_client.get("/api/protected")
    assert res_missing.status_code == 401

# --- Requirement 6: Sliding Window Rate Limiting (Redis Sorted Sets) ---
@pytest.mark.asyncio
async def test_req6_sliding_window_rate_limiting(http_client):
    # Issue key with rateLimitPerMinute = 5
    issue_res = await http_client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 5})
    assert issue_res.status_code == 201
    api_key = issue_res.json()["apiKey"]

    headers = {"Authorization": f"Bearer {api_key}"}

    # First 5 requests should return 200 OK
    for i in range(5):
        res = await http_client.get("/api/protected", headers=headers)
        assert res.status_code == 200, f"Request {i+1} failed with status {res.status_code}"

    # 6th request within the same 60-second window must return 429 Too Many Requests
    res_6th = await http_client.get("/api/protected", headers=headers)
    assert res_6th.status_code == 429
    assert "Retry-After" in res_6th.headers
    retry_after = int(res_6th.headers["Retry-After"])
    assert 1 <= retry_after <= 60

# --- Requirement 7: API Key Revocation ---
@pytest.mark.asyncio
async def test_req7_api_key_revocation(http_client):
    # Issue new key
    issue_res = await http_client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 100})
    issued_data = issue_res.json()
    api_key = issued_data["apiKey"]
    key_id = issued_data["keyRecord"]["id"]

    # Verify key works initially
    res_ok = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {api_key}"})
    assert res_ok.status_code == 200

    # Call DELETE /api/keys/:keyId
    del_res = await http_client.delete(f"/api/keys/{key_id}")
    assert del_res.status_code == 204

    # Immediately verify key is rejected with 401 Unauthorized
    res_revoked = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {api_key}"})
    assert res_revoked.status_code == 401

# --- Requirement 8: API Key Rotation with Grace Period ---
@pytest.mark.asyncio
async def test_req8_key_rotation_grace_period(http_client, db_conn):
    # Issue initial key (key_A)
    issue_res = await http_client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 100})
    key_a = issue_res.json()["apiKey"]
    key_a_id = issue_res.json()["keyRecord"]["id"]

    # Rotate key_A to generate key_B
    rotate_res = await http_client.post(f"/api/keys/{key_a_id}/rotate")
    assert rotate_res.status_code == 200
    key_b = rotate_res.json()["newApiKey"]
    assert key_b != key_a

    # Immediately hit /api/protected with both key_A and key_B (both must be 200 OK)
    res_a = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {key_a}"})
    assert res_a.status_code == 200

    res_b = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {key_b}"})
    assert res_b.status_code == 200

    # Simulate expiration by setting key_A expires_at to 2 seconds in the past in the database
    # (Testing clock advancement without having to sleep full 65 seconds in test suite)
    await db_conn.execute("UPDATE api_keys SET expires_at = NOW() - INTERVAL '2 seconds' WHERE id = $1", key_a_id)

    # Now key_A must return 401 Unauthorized
    res_a_expired = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {key_a}"})
    assert res_a_expired.status_code == 401

    # While key_B continues to succeed with 200 OK
    res_b_still_valid = await http_client.get("/api/protected", headers={"Authorization": f"Bearer {key_b}"})
    assert res_b_still_valid.status_code == 200

# --- Requirement 9: Audit Logging ---
@pytest.mark.asyncio
async def test_req9_audit_logging(http_client, db_conn):
    # Count audit logs before
    initial_count = await db_conn.fetchval("SELECT COUNT(*) FROM audit_logs")

    # Issue a new key with rateLimitPerMinute = 1
    issue_res = await http_client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 1})
    key_data = issue_res.json()
    api_key = key_data["apiKey"]
    key_id = key_data["keyRecord"]["id"]

    headers = {"Authorization": f"Bearer {api_key}"}

    # 1. Successful request (200 OK)
    res_ok = await http_client.get("/api/protected", headers=headers)
    assert res_ok.status_code == 200

    # Verify audit_logs count increased by 1 and logged 200
    count_after_ok = await db_conn.fetchval("SELECT COUNT(*) FROM audit_logs")
    assert count_after_ok == initial_count + 1

    latest_log_1 = await db_conn.fetchrow("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 1")
    assert latest_log_1["api_key_id"] == key_id
    assert latest_log_1["endpoint"] == "/api/protected"
    assert latest_log_1["status_code"] == 200

    # 2. Rate-limited request (429 Too Many Requests)
    res_limited = await http_client.get("/api/protected", headers=headers)
    assert res_limited.status_code == 429

    # Verify audit_logs count increased by 1 again and logged 429
    count_after_limited = await db_conn.fetchval("SELECT COUNT(*) FROM audit_logs")
    assert count_after_limited == count_after_ok + 1

    latest_log_2 = await db_conn.fetchrow("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 1")
    assert latest_log_2["api_key_id"] == key_id
    assert latest_log_2["endpoint"] == "/api/protected"
    assert latest_log_2["status_code"] == 429
