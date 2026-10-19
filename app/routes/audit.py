from typing import Optional
from fastapi import APIRouter, Query, status
from app.database import get_audit_logs, get_analytics_summary

router = APIRouter(tags=["Audit & Analytics"])

@router.get(
    "/api/audit-logs",
    status_code=status.HTTP_200_OK,
    summary="Get paginated audit logs for audit console"
)
async def list_audit_logs(
    tenant_id: Optional[int] = Query(None, alias="tenantId"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100)
):
    """
    Returns paginated audit log entries with masked keys, endpoint, status code, and timestamp.
    """
    offset = (page - 1) * limit
    result = await get_audit_logs(tenant_id=tenant_id, limit=limit, offset=offset)
    total = result["total"]
    total_pages = (total + limit - 1) // limit if total > 0 else 1

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": total_pages,
        "items": result["items"]
    }

@router.get(
    "/api/analytics/usage",
    status_code=status.HTTP_200_OK,
    summary="Get request volume and breakdown over time for charts"
)
async def get_usage_analytics(
    tenant_id: Optional[int] = Query(None, alias="tenantId")
):
    """
    Returns usage metrics (total, 200 OK, 429 Rate Limited, active keys)
    and time series data for rendering the usage visualization chart.
    """
    return await get_analytics_summary(tenant_id=tenant_id)
