from bson import ObjectId
from fastapi import Depends, HTTPException, Request, status
import pymongo.errors

from app.auth.router import oauth2_scheme
from app.db.database import db
from app.utils.security import decode_token, COOKIE_NAME
from app.utils.user_status import is_auth_status_allowed

ROLE_ALIASES = {
    "super-admin": "super_admin",
    "super_admin": "super_admin",
}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role, role)


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
):
    # Cookie-first, then Authorization header
    cookie_token = request.cookies.get(COOKIE_NAME)
    auth_token = cookie_token or token
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(auth_token)

    user_id = payload.get("user_id")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Retry once on AutoReconnect — Atlas may have dropped the idle connection
    # during a long Ollama benchmark run (8+ min). Motor's heartbeat should keep
    # it alive, but if it does drop, one retry is enough to reconnect.
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except pymongo.errors.AutoReconnect:
        try:
            user = await db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            raise HTTPException(status_code=503, detail="Database temporarily unavailable. Please retry.")
    if not user or not is_auth_status_allowed(user.get("status")):
        raise HTTPException(status_code=401, detail="User not found or inactive")

    role = normalize_role(user.get("role", ""))
    tenant_id = user.get("tenantId")
    student_id = None
    teacher_id = None
    admin_id = None
    super_admin_id = None

    role_map = {"teacher": db.teachers, "student": db.students, "admin": db.admins}
    coll = role_map.get(role)
    if coll is not None:
        role_doc_query = {"userId": user["_id"]}
        if role != "student" and user.get("tenantId"):
            role_doc_query["tenantId"] = user.get("tenantId")

        role_doc = await coll.find_one(role_doc_query)
        if not role_doc:
            role_doc = await coll.find_one(
                {"userId": user["_id"]},
                sort=[("updatedAt", -1), ("_id", -1)],
            )
        if role_doc:
            tenant_id = role_doc.get("tenantId") or tenant_id
            role_doc_id = str(role_doc.get("_id"))
            if role == "student":
                student_id = role_doc_id
            elif role == "teacher":
                teacher_id = role_doc_id
            elif role == "admin":
                admin_id = role_doc_id

    if role == "super_admin":
        super_admin_id = str(user["_id"])
    elif role == "student":
        tenant_id = None

    return {
        "user_id": str(user["_id"]),
        "role": role,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "student_id": student_id,
        "teacher_id": teacher_id,
        "admin_id": admin_id,
        "super_admin_id": super_admin_id,
    }


def require_role(*allowed_roles: str):
    normalized_roles = {normalize_role(role) for role in allowed_roles}

    def role_checker(current_user=Depends(get_current_user)):
        if normalize_role(current_user["role"]) not in normalized_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Insufficient Role",
            )
        return current_user

    return role_checker


def require_tenant(current_user=Depends(get_current_user)):
    if not current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Tenant context required")
    return current_user
