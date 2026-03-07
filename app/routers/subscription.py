from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, require_role
from app.crud.subscription import (
    create_subscription as crud_create_sub,
    delete_subscription as crud_delete_sub,
    fetch_subscription_by_tenant,
    fetch_subscriptions,
    update_subscription as crud_update_sub,
)
from app.schemas.subscription import Subscription

router = APIRouter(
    prefix="/subscriptions",
    tags=["Subscriptions"],
    dependencies=[Depends(require_role("admin", "super_admin"))],
)


def _enforce_subscription_scope(current_user: dict, tenant_id: str):
    if current_user["role"] == "super_admin":
        return
    if current_user.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )

# Get all subscriptions
@router.get("/", response_model=List[Subscription])
async def get_subscriptions(current_user=Depends(get_current_user)):
    tenant_id = None if current_user["role"] == "super_admin" else current_user.get("tenant_id")
    return await fetch_subscriptions(tenant_id=tenant_id)

# Get subscription by tenant_id
@router.get("/{tenant_id}", response_model=Subscription)
async def get_subscription(tenant_id: str, current_user=Depends(get_current_user)):
    _enforce_subscription_scope(current_user, tenant_id)
    sub = await fetch_subscription_by_tenant(tenant_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub

# Create a new subscription
@router.post("/", response_model=Subscription)
async def create_subscription(sub: Subscription, current_user=Depends(get_current_user)):
    _enforce_subscription_scope(current_user, sub.tenantId)
    return await crud_create_sub(sub)

# Update subscription by tenantId
@router.put("/{tenant_id}", response_model=Subscription)
async def update_subscription(
    tenant_id: str, sub: Subscription, current_user=Depends(get_current_user)
):
    _enforce_subscription_scope(current_user, tenant_id)
    if sub.tenantId != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body tenantId must match path tenant_id",
        )
    updated_sub = await crud_update_sub(tenant_id, sub)
    if not updated_sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return updated_sub

# Delete subscription by tenantId
@router.delete("/{tenant_id}")
async def delete_subscription(tenant_id: str, current_user=Depends(get_current_user)):
    _enforce_subscription_scope(current_user, tenant_id)
    success = await crud_delete_sub(tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"detail": "Subscription deleted successfully"}
