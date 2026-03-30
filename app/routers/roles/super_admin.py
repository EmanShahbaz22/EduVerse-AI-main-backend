from fastapi import APIRouter, Depends, HTTPException
from app.auth.dependencies import get_current_user, require_role
from app.schemas.super_admin import SuperAdminResponse, SuperAdminUpdate, SuperAdminDashboardResponse
from app.crud.super_admin import get_superadmin_by_user, update_superadmin, get_super_admin_dashboard_stats

router = APIRouter(
    prefix="/super-admin",
    tags=["Super Admin"],
    dependencies=[Depends(require_role("super_admin"))],
)


@router.get("/me", response_model=SuperAdminResponse)
async def get_my_profile(current_user=Depends(get_current_user)):

    super_admin = await get_superadmin_by_user(current_user["user_id"])
    if not super_admin:
        raise HTTPException(404, "Super Admin profile not found")

    return super_admin


@router.patch("/me", response_model=SuperAdminResponse)
async def update_my_profile(
    update: SuperAdminUpdate, current_user=Depends(get_current_user)
):

    updated = await update_superadmin(
        current_user["user_id"], update.dict(exclude_unset=True)
    )

    if not updated:
        raise HTTPException(404, "Super Admin profile not found")

    return updated

@router.get("/dashboard/stats", response_model=SuperAdminDashboardResponse)
async def get_dashboard_data(current_user=Depends(get_current_user)):
    # Requires super_admin role middleware to pass (configured in router router)
    stats = await get_super_admin_dashboard_stats()
    return stats
