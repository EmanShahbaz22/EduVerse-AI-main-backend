from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, require_role
from app.db.database import db
from app.schemas.users import UserCreate

router = APIRouter(prefix="/auth/teacher", tags=["Teacher Authentication"])


@router.post("/signup")
async def signup_teacher(_payload: UserCreate):
    raise HTTPException(
        status_code=403,
        detail="Teacher self-signup is disabled. Please contact your tenant administrator.",
    )


class TeacherTenantSwitchRequest(BaseModel):
    tenantId: str


@router.get("/tenants")
async def list_teacher_tenants(current_user=Depends(require_role("teacher"))):
    user_id = current_user.get("user_id")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Invalid user context")

    user_oid = ObjectId(user_id)
    active_tenant_id = current_user.get("tenant_id")

    teacher_docs = await db.teachers.find({"userId": user_oid}).to_list(200)
    if not teacher_docs:
        return {"activeTenantId": active_tenant_id, "tenants": []}

    tenant_ids = []
    seen = set()
    for doc in teacher_docs:
        tenant_val = doc.get("tenantId")
        tenant_oid = tenant_val if isinstance(tenant_val, ObjectId) else None
        if tenant_oid and tenant_oid not in seen:
            seen.add(tenant_oid)
            tenant_ids.append(tenant_oid)

    tenant_docs = await db.tenants.find({"_id": {"$in": tenant_ids}}).to_list(200)
    tenant_map = {str(t["_id"]): t for t in tenant_docs}

    items = []
    for doc in teacher_docs:
        tenant_val = doc.get("tenantId")
        if not isinstance(tenant_val, ObjectId):
            continue
        tenant_id = str(tenant_val)
        tenant_doc = tenant_map.get(tenant_id)
        items.append(
            {
                "tenantId": tenant_id,
                "tenantName": (
                    tenant_doc.get("tenantName", "Unknown Tenant")
                    if tenant_doc
                    else "Unknown Tenant"
                ),
                "teacherId": str(doc.get("_id")),
                "active": tenant_id == active_tenant_id,
            }
        )

    items.sort(key=lambda x: (not x["active"], x["tenantName"].lower()))
    return {"activeTenantId": active_tenant_id, "tenants": items}


@router.post("/switch-tenant")
async def switch_teacher_tenant(
    payload: TeacherTenantSwitchRequest,
    current_user=Depends(require_role("teacher")),
):
    if not ObjectId.is_valid(payload.tenantId):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tenantId",
        )
    user_id = current_user.get("user_id")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Invalid user context")

    user_oid = ObjectId(user_id)
    tenant_oid = ObjectId(payload.tenantId)

    teacher = await db.teachers.find_one({"userId": user_oid, "tenantId": tenant_oid})
    if not teacher:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not linked to this tenant",
        )

    tenant = await db.tenants.find_one({"_id": tenant_oid})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    await db.users.update_one(
        {"_id": user_oid},
        {"$set": {"tenantId": tenant_oid, "updatedAt": datetime.utcnow()}},
    )

    return {
        "message": "Tenant context updated successfully",
        "tenantId": str(tenant_oid),
        "tenantName": tenant.get("tenantName", ""),
        "teacherId": str(teacher["_id"]),
    }
