from typing import List
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.database import get_tenants, get_pool

router = APIRouter(tags=["Tenants"])

class TenantItem(BaseModel):
    id: int
    name: str
    createdAt: str

class CreateTenantRequest(BaseModel):
    name: str

@router.get(
    "/api/tenants",
    response_model=List[TenantItem],
    status_code=status.HTTP_200_OK,
    summary="List all tenants"
)
async def list_tenants():
    rows = await get_tenants()
    return [
        TenantItem(
            id=r["id"],
            name=r["name"],
            createdAt=r["created_at"].isoformat() + "Z" if r["created_at"] else ""
        )
        for r in rows
    ]

@router.post(
    "/api/tenants",
    response_model=TenantItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant"
)
async def create_tenant(body: CreateTenantRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant name cannot be empty."
        )

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO tenants (name, created_at)
            VALUES ($1, NOW())
            RETURNING id, name, created_at
        """, name)
        return TenantItem(
            id=row["id"],
            name=row["name"],
            createdAt=row["created_at"].isoformat() + "Z" if row["created_at"] else ""
        )
