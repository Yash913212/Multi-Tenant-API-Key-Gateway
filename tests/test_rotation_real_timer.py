"""
Test verifying the full 65-second real-world timer for Key Rotation Grace Period.
"""

import asyncio
import os
import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")

@pytest.mark.asyncio
async def test_rotation_grace_period_real_clock():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as client:
        # 1. Issue a key (key_A)
        issue_res = await client.post("/api/tenants/1/keys", json={"rateLimitPerMinute": 100})
        assert issue_res.status_code == 201
        data_a = issue_res.json()
        key_a = data_a["apiKey"]
        key_a_id = data_a["keyRecord"]["id"]

        # 2. Call the rotate endpoint to get key_B
        rotate_res = await client.post(f"/api/keys/{key_a_id}/rotate")
        assert rotate_res.status_code == 200
        key_b = rotate_res.json()["newApiKey"]

        # 3. Immediately hit /api/protected with key_A (should be 200 OK)
        res_a1 = await client.get("/api/protected", headers={"Authorization": f"Bearer {key_a}"})
        assert res_a1.status_code == 200, f"Expected 200 for key_A during grace period, got {res_a1.status_code}"

        # 4. Immediately hit /api/protected with key_B (should be 200 OK)
        res_b1 = await client.get("/api/protected", headers={"Authorization": f"Bearer {key_b}"})
        assert res_b1.status_code == 200, f"Expected 200 for key_B, got {res_b1.status_code}"

        print("\n[Timer Test] Waiting 65 seconds for rotation grace period to expire naturally...")
        await asyncio.sleep(65)

        # 5. Hit /api/protected with key_A again (should be 401 Unauthorized)
        res_a2 = await client.get("/api/protected", headers={"Authorization": f"Bearer {key_a}"})
        assert res_a2.status_code == 401, f"Expected 401 for expired key_A, got {res_a2.status_code}"

        # 6. Hit /api/protected with key_B again (should be 200 OK)
        res_b2 = await client.get("/api/protected", headers={"Authorization": f"Bearer {key_b}"})
        assert res_b2.status_code == 200, f"Expected 200 for active key_B, got {res_b2.status_code}"

        print("[Timer Test] 65-second rotation grace period test passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_rotation_grace_period_real_clock())
