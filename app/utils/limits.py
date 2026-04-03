from bson import ObjectId
from fastapi import HTTPException, status
from typing import Literal

from app.db.database import db

ResourceType = Literal["students", "teachers", "courses", "storage"]

async def check_tenant_limits(tenant_id: str | ObjectId, resource_type: ResourceType, additional_value: float = 1):
    """
    Checks if a given action will exceed the tenant's exact subscription limit.
    additional_value: For storage, this represents the incoming file size in bytes.
                      For other types, it represents the number of items being created (usually 1).
    """
    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id), "isDeleted": {"$ne": True}})
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
        
    # --- Expiry & Grace Period Enforcement ---
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    expiry = tenant.get("subscriptionExpiryDate")
    manual_grace = tenant.get("gracePeriodUntil")
    status_val = tenant.get("status", "active")
    
    if status_val != "active":
        raise HTTPException(status_code=403, detail="Tenant account is not active.")
        
    is_active = False
    # 1. Check manual grace (priority)
    if manual_grace and now < manual_grace:
        is_active = True
    # 2. Check standard expiry + 48h auto-grace
    elif expiry:
        if now < (expiry + timedelta(hours=48)):
            is_active = True
    else:
        # No expiry set -> assume active (legacy/unlimited)
        is_active = True

    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Subscription expired. Please renew to continue."
        )
    # ------------------------------------------
        
    subscription_id = tenant.get("subscriptionId")
    if not subscription_id:
        plan = None
    else:
        plan = await db.subscriptionPlans.find_one({"_id": ObjectId(subscription_id), "isDeleted": {"$ne": True}})
        
    # Default strict free plan fallback if tenant has no active plan
    if not plan:
        plan = {
            "name": "Basic Default API",
            "maxStudents": 100,
            "maxTeachers": 20,
            "maxCourses": 100,
            "storageGb": 10,
        }
        
    if resource_type == "students":
        limit = plan.get("maxStudents")
        if limit is not None and limit >= 0:
            current_count = await db.students.count_documents({"tenantId": ObjectId(tenant_id)})
            if current_count + additional_value > limit:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Subscription limit reached: Maximum {limit} students allowed on the {plan.get('name')} tier."
                )

    elif resource_type == "teachers":
        limit = plan.get("maxTeachers")
        if limit is not None and limit >= 0:
            current_count = await db.teachers.count_documents({"tenantId": ObjectId(tenant_id)})
            if current_count + additional_value > limit:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Subscription limit reached: Maximum {limit} teachers allowed on the {plan.get('name')} tier."
                )

    elif resource_type == "courses":
        limit = plan.get("maxCourses")
        if limit is not None and limit >= 0:
            current_count = await db.courses.count_documents({"tenantId": ObjectId(tenant_id)})
            if current_count + additional_value > limit:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Subscription limit reached: Maximum {limit} courses allowed on the {plan.get('name')} tier."
                )

    elif resource_type == "storage":
        limit_gb = plan.get("storageGb")
        if limit_gb is not None and limit_gb >= 0:
            limit_bytes = limit_gb * 1024 * 1024 * 1024
            
            current_storage = tenant.get("totalStorageUsedBytes", 0)
            if current_storage + additional_value > limit_bytes:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Storage limit reached: Maximum {limit_gb} GB allowed on the {plan.get('name')} tier."
                )
