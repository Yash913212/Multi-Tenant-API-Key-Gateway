from typing import List, Optional
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from app.config import settings
from app.database import (
    get_tenant_by_id,
    insert_api_key,
    get_keys_for_tenant,
    get_key_by_id,
    revoke_api_key,
    rotate_api_key,
)
from app.security import generate_api_key, mask_api_key

router = APIRouter(tags=["API Keys"])

class CreateKeyRequest(BaseModel):
    rateLimitPerMinute: int = Field(default=100, ge=1, le=100000)

    model_config = {
        "populate_by_name": True,
        "extra": "ignore"
    }

class KeyRecordResponse(BaseModel):
    id: int
    lastFour: str
    rateLimitPerMinute: int

class IssueKeyResponse(BaseModel):
    apiKey: str
    keyRecord: KeyRecordResponse

class KeyListItem(BaseModel):
    id: int
    maskedKey: str
    createdAt: str
    isActive: bool
    rateLimitPerMinute: Optional[int] = None
    expiresAt: Optional[str] = None

class RotateKeyResponse(BaseModel):
    newApiKey: str

@router.post(
    "/api/tenants/{tenant_id}/keys",
    response_model=IssueKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new API key for a tenant"
)
async def issue_api_key(
    tenant_id: int,
    body: Optional[CreateKeyRequest] = None
):
    """
    Issues a new cryptographically secure API key for the given tenant.
    The raw key is returned exactly once in the response.
    Only the SHA-256 hash is persisted in the database.
    """
    rate_limit = body.rateLimitPerMinute if body else settings.DEFAULT_RATE_LIMIT

    # Validate tenant existence
    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant with ID {tenant_id} not found."
        )

    # Cryptographically generate key and SHA-256 hash
    full_key, key_hash, prefix, last_four = generate_api_key(prefix=settings.DEFAULT_KEY_PREFIX)

    # Persist hashed record
    key_record = await insert_api_key(
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=prefix,
        last_four=last_four,
        rate_limit_per_minute=rate_limit
    )

    return IssueKeyResponse(
        apiKey=full_key,
        keyRecord=KeyRecordResponse(
            id=key_record["id"],
            lastFour=key_record["last_four"],
            rateLimitPerMinute=key_record["rate_limit_per_minute"]
        )
    )

@router.get(
    "/api/tenants/{tenant_id}/keys",
    response_model=List[KeyListItem],
    status_code=status.HTTP_200_OK,
    summary="List all API keys for a tenant"
)
async def list_tenant_keys(tenant_id: int):
    """
    Lists all API keys for a tenant with masked key values (e.g. sk_live_...3f9a).
    Plaintext keys are never retrievable.
    """
    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant with ID {tenant_id} not found."
        )

    rows = await get_keys_for_tenant(tenant_id)
    response_list = []
    for r in rows:
        masked = mask_api_key(r["key_prefix"], r["last_four"])
        created_str = r["created_at"].isoformat() + "Z" if r["created_at"] else ""
        expires_str = r["expires_at"].isoformat() + "Z" if r["expires_at"] else None
        response_list.append(
            KeyListItem(
                id=r["id"],
                maskedKey=masked,
                createdAt=created_str,
                isActive=r["is_active"],
                rateLimitPerMinute=r["rate_limit_per_minute"],
                expiresAt=expires_str
            )
        )
    return response_list

@router.delete(
    "/api/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key immediately"
)
async def revoke_key(key_id: int):
    """
    Immediately revokes an API key.
    Subsequent requests authenticated with this key will fail with 401 Unauthorized.
    """
    key = await get_key_by_id(key_id)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API Key with ID {key_id} not found."
        )

    await revoke_api_key(key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post(
    "/api/keys/{key_id}/rotate",
    response_model=RotateKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate an API key with grace period"
)
async def rotate_key(key_id: int):
    """
    Rotates an API key with a 1-minute grace period.
    The old key remains valid until expires_at (NOW + 1 min), while the new key
    is immediately active. After 1 minute, requests with the old key return 401.
    """
    old_key = await get_key_by_id(key_id)
    if not old_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API Key with ID {key_id} not found."
        )

    if not old_key["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot rotate an inactive or revoked API key."
        )

    # Generate new key
    full_new_key, new_hash, new_prefix, new_last_four = generate_api_key(prefix=old_key["key_prefix"])

    # Rotate transactionally with configured grace period (default 60 seconds)
    await rotate_api_key(
        old_key_id=key_id,
        tenant_id=old_key["tenant_id"],
        rate_limit_per_minute=old_key["rate_limit_per_minute"],
        new_key_hash=new_hash,
        new_key_prefix=new_prefix,
        new_last_four=new_last_four,
        grace_period_seconds=settings.ROTATION_GRACE_PERIOD_SECONDS
    )

    return RotateKeyResponse(newApiKey=full_new_key)
