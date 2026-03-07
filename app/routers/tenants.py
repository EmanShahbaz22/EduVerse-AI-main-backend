from fastapi import HTTPException, status, APIRouter, Query, Depends
from app.auth.dependencies import require_role
from bson import ObjectId
from typing import Optional
from app.schemas.tenants import TenantResponse, TenantCreate, TenantUpdate
from app.crud.tenants import (
    create_tenant,
    get_all_tenants,
    delete_tenant,
    get_tenant,
    update_tenant,
    get_tenant_dashboard_overview,
)

router = APIRouter(
    prefix="/tenants",
    tags=["Tenants"],
    dependencies=[Depends(require_role("super_admin"))]
)

# -------------------------
# Validate ObjectId before using it
# -------------------------
def _validate_objectid(_id: str):
    if not ObjectId.is_valid(_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ObjectId"
        )

# # -------------------------
# # Create a new tenant
# # -------------------------
# @router.post("/", response_model=TenantResponse, summary="Create a new tenant")
# async def create_tenant_route(data: TenantCreate):
#     # let CRUD validate subscriptionId & duplicates
#     return await create_tenant(data)


# -------------------------
# Get all tenants (search, filter, sort, pagination)
# -------------------------
@router.get("/", response_model=list[TenantResponse], summary="Get all tenants")
async def get_all(
    skip: int = Query(0, ge=0, description="Items to skip for pagination"),
    limit: int = Query(10, ge=1, le=100, description="Max tenants to return"),
    status: Optional[str] = Query(None, description="Filter tenants by status"),
    search: Optional[str] = Query(None, description="Search tenants by tenant name or admin email"),
    sort: Optional[str] = Query(None, description="Sort results: 'name' or 'createdAt or '-createdAt'"),
    planCode: Optional[str] = Query(None, description="Filter by assigned subscription plan code"),
    category: Optional[str] = Query(None, description="Filter by subscription category"),
):
    return await get_all_tenants(
        skip=skip,
        limit=limit,
        status=status,
        search=search,
        sort=sort,
        plan_code=planCode,
        plan_category=category,
    )


@router.get("/dashboard/overview", summary="Get super-admin dashboard overview")
async def get_dashboard_overview(
    months: int = Query(6, ge=1, le=24),
    topN: int = Query(5, ge=1, le=20),
):
    return await get_tenant_dashboard_overview(months=months, top_n=topN)


# -------------------------
# Get a single tenant by ID
# -------------------------
@router.get("/{tenant_id}", response_model=TenantResponse, summary="Get tenant by ID")
async def get_one(tenant_id: str):
    _validate_objectid(tenant_id)

    tenant = await get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return tenant


# -------------------------
# Update a tenant
# -------------------------
@router.patch("/{tenant_id}", response_model=TenantResponse, summary="Patch/Update tenant")
async def update_one(tenant_id: str, data: TenantUpdate):
    _validate_objectid(tenant_id)

    result = await update_tenant(tenant_id, data.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return result

# -------------------------
# Delete a tenant
# -------------------------
@router.delete("/{tenant_id}", summary="Delete tenant")
async def delete_one(tenant_id: str):
    _validate_objectid(tenant_id)

    if not await delete_tenant(tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return {"message": "Tenant deleted successfully"}
