from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import require_role
from app.crud.subscription_plans import (
    create_subscription_plan,
    delete_subscription_plan,
    get_subscription_plan,
    list_subscription_plans,
    update_subscription_plan,
)
from app.schemas.subscription_plans import (
    SubscriptionPlanCreate,
    SubscriptionPlanResponse,
    SubscriptionPlanUpdate,
)

router = APIRouter(
    prefix="/subscription-plans",
    tags=["Subscription Plans"],
)


@router.get("/public", response_model=list[SubscriptionPlanResponse])
async def list_public_plans(
    status_filter: Optional[str] = Query(
        default="active", alias="status", description="Filter by active/inactive status"
    )
):
    plans = await list_subscription_plans(status_filter=status_filter or "active")
    return sorted(plans, key=lambda plan: (plan.get("pricePerMonth", 0), plan.get("name", "")))


@router.get("/public/{plan_id}", response_model=SubscriptionPlanResponse)
async def get_public_plan(plan_id: str):
    plan = await get_subscription_plan(plan_id)
    if not plan or plan.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )
    return plan


@router.get("/", response_model=list[SubscriptionPlanResponse])
async def list_plans(
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="Filter by active/inactive status"
    ),
    current_user=Depends(require_role("super_admin")),
):
    return await list_subscription_plans(status_filter=status_filter)


@router.get("/{plan_id}", response_model=SubscriptionPlanResponse)
async def get_plan(plan_id: str, current_user=Depends(require_role("super_admin"))):
    plan = await get_subscription_plan(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )
    return plan


@router.post("/", response_model=SubscriptionPlanResponse, status_code=201)
async def create_plan(
    payload: SubscriptionPlanCreate, current_user=Depends(require_role("super_admin"))
):
    return await create_subscription_plan(payload.model_dump())


@router.patch("/{plan_id}", response_model=SubscriptionPlanResponse)
async def update_plan(
    plan_id: str,
    payload: SubscriptionPlanUpdate,
    current_user=Depends(require_role("super_admin")),
):
    updated = await update_subscription_plan(plan_id, payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )
    return updated


@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: str, current_user=Depends(require_role("super_admin"))
):
    deleted = await delete_subscription_plan(plan_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )
    return {"message": "Subscription plan deleted successfully"}
