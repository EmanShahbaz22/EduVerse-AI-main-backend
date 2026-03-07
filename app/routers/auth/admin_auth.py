from fastapi import APIRouter, HTTPException, status
from bson import ObjectId
from pydantic import ValidationError
from app.schemas.tenants import TenantCreate
from app.schemas.users import AdminSignupRequest
from app.crud import users, admins
from app.crud.tenants import create_tenant
from app.db.database import db

router = APIRouter(prefix="/auth/admin", tags=["Admin Authentication"])


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup_admin(payload: AdminSignupRequest):
    if payload.role != "admin":
        raise HTTPException(403, "This endpoint is only for admin signup")
    if not payload.tenantName.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization name is required",
        )

    # Create Tenant using tenant CRUD
    tenant_logo = (payload.tenantLogoUrl or "").strip() or None
    tenant_data = {
        "tenantName": payload.tenantName.strip(),
        "tenantLogoUrl": tenant_logo,
        "adminEmail": payload.email,
        # "subscriptionId": payload.subscriptionId | None,
    }
    try:
        tenant = await create_tenant(
            TenantCreate(**{k: v for k, v in tenant_data.items() if v is not None})
        )
    except ValidationError as exc:
        first_error = exc.errors()[0]["msg"] if exc.errors() else "Invalid tenant details."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=first_error,
        )

    user_data = payload.model_dump()
    user_data["role"] = "admin"  # assign role internally
    user_data["tenantId"] = tenant["id"]  # assign created tenant
    user_data["profileImageURL"] = (user_data.get("profileImageURL") or "").strip() or None
    try:
        user = await users.create_user(user_data)
    except ValueError as exc:
        await db.tenants.delete_one({"_id": ObjectId(tenant["id"])})
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        await admins.create_admin_profile(user["id"], tenant["id"])
    except Exception:
        await db.users.delete_one({"_id": ObjectId(user["id"])})
        await db.tenants.delete_one({"_id": ObjectId(tenant["id"])})
        raise HTTPException(
            status_code=500,
            detail="Unable to complete admin registration right now. Please try again.",
        )

    return {
        "message": "Admin and tenant created successfully",
        "user": user,
        "tenant": tenant,
    }
