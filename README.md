# Multi-Tenant API Key Management Gateway & Audit Service

A high-performance, secure, multi-tenant API key management gateway built from scratch in Python (FastAPI, Redis, PostgreSQL, and Docker).

## Architectural Overview

```
                      +-----------------------------+
                      |   Client Application / UI   |
                      +--------------+--------------+
                                     |
                                     | (Bearer sk_live_...)
                                     v
+-------------------------------------------------------------------------+
|                           API Gateway Service                           |
|  - Cryptographic key generation (secrets, URL-safe Base64, prefix)      |
|  - Constant-time / SHA-256 key hashing & authentication                 |
|  - 1-minute key rotation grace period enforcement                       |
|  - Sliding-window rate limiter using Redis sorted sets (first principles)|
|  - Audit logging for all authenticated attempts (200 & 429)             |
+--------------------+-------------------------------+--------------------+
                     |                               |
          (SQL Queries / Audit)           (ZREMRANGE, ZADD, ZCARD)
                     v                               v
       +---------------------------+   +---------------------------+
       |   PostgreSQL Database     |   |       Redis Cache         |
       |  - tenants                |   |  - Sorted Sets:           |
       |  - api_keys (hashed only) |   |    rate_limit:{apiKeyId}  |
       |  - audit_logs             |   +---------------------------+
       +---------------------------+
```

---

## Key Features

1. **Secure API Key Lifecycle**:
   - **Generation**: Cryptographically secure 24 random bytes (`secrets.token_bytes`), encoded with Base64 URL-safe encoding (RFC 4648 §5), prefixed with `sk_live_`.
   - **Storage**: Keys are never stored in plaintext. Only the SHA-256 hexadecimal hash is stored in PostgreSQL.
   - **Single Reveal**: Plaintext keys are returned exactly once upon creation or rotation.
   - **Masking**: Keys are listed in the console and APIs in masked format: `sk_live_...<last_four>`.

2. **First-Principles Sliding-Window Rate Limiting**:
   - Built directly on Redis **Sorted Sets** (`ZREMRANGEBYSCORE`, `ZADD`, `ZCARD`) within an atomic `MULTI / EXEC` transaction.
   - Prunes timestamps outside the 60-second sliding window.
   - Strictly monotonic microsecond timestamps eliminate score collisions in high-concurrency bursts.
   - Returns HTTP `429 Too Many Requests` with a calculated `Retry-After` header indicating exact seconds until the oldest request in the window expires.

3. **Key Rotation with Grace Period**:
   - Rotates keys transactionally (`POST /api/keys/:keyId/rotate`).
   - Old key remains active for a 60-second grace period (`expires_at = NOW() + INTERVAL '1 minute'`).
   - Both keys authenticate successfully (`200 OK`) during the grace period; once expired, the old key immediately returns `401 Unauthorized`.

4. **Instant Revocation**:
   - `DELETE /api/keys/:keyId` flips `is_active` to `FALSE` immediately (`204 No Content`).
   - Subsequent requests with this key immediately return `401 Unauthorized`.

5. **Comprehensive Audit Logs**:
   - All authenticated requests to `/api/protected` (both `200 OK` and `429 Too Many Requests`) are persisted in the `audit_logs` table.

6. **Interactive Management Console**:
   - Modern glassmorphism UI with dark theme and responsive layout.
   - Live system health status badge (API Gateway, PostgreSQL, and Redis connectivity).
   - Real-time request volume chart powered by Chart.js (200 OK vs 429 Rate Limited).
   - Key management table with rotate and revoke confirmation modals.
   - Built-in **Gateway Request Simulator** to test single requests or fire 6-request rapid bursts to see sliding-window rate limiting live.
   - Paginated, auto-refreshing audit logs table.

---

## Quickstart with Docker Compose

Ensure Docker Desktop is running, then run:

```bash
docker compose up -d --build
```

### Verify Container Health
```bash
docker compose ps
```
All three services (`api`, `db`, `redis`) will show status `healthy`.

### Open the Console
Navigate to [http://localhost:8000](http://localhost:8000) in your web browser.

---

## API Specifications

### 1. Issue API Key
- **Endpoint**: `POST /api/tenants/:tenantId/keys`
- **Request Body**:
  ```json
  {
    "rateLimitPerMinute": 100
  }
  ```
- **Response** (`201 Created`):
  ```json
  {
    "apiKey": "sk_live_vV2m4a1K...",
    "keyRecord": {
      "id": 1,
      "lastFour": "7x9q",
      "rateLimitPerMinute": 100
    }
  }
  ```

### 2. List Tenant Keys
- **Endpoint**: `GET /api/tenants/:tenantId/keys`
- **Response** (`200 OK`):
  ```json
  [
    {
      "id": 1,
      "maskedKey": "sk_live_...7x9q",
      "createdAt": "2026-10-19T16:50:00Z",
      "isActive": true
    }
  ]
  ```

### 3. Access Protected Endpoint
- **Endpoint**: `GET /api/protected`
- **Header**: `Authorization: Bearer <plaintext_api_key>`
- **Response (Success - 200 OK)**:
  ```json
  {
    "message": "Access granted to protected resource",
    "tenantId": 1,
    "apiKeyId": 1,
    "quotaUsed": 1,
    "rateLimitPerMinute": 100
  }
  ```
- **Response (Rate Limited - 429 Too Many Requests)**:
  - Header: `Retry-After: 48`
  ```json
  {
    "detail": "Rate limit of 5 requests per minute exceeded.",
    "retryAfter": 48,
    "currentRequests": 6
  }
  ```

### 4. Rotate API Key
- **Endpoint**: `POST /api/keys/:keyId/rotate`
- **Response** (`200 OK`):
  ```json
  {
    "newApiKey": "sk_live_9uB1m..."
  }
  ```

### 5. Revoke API Key
- **Endpoint**: `DELETE /api/keys/:keyId`
- **Response** (`204 No Content`)

---

## Running Automated Tests

Run the full end-to-end test suite verifying all 9 Core Requirements:

```bash
docker compose exec api pytest tests/test_gateway.py -v
```
Or directly from host with Python 3:
```bash
pytest tests/test_gateway.py -v
```
