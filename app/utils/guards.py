from fastapi import Depends, HTTPException

from app.auth.dependencies import get_current_user


def admin_guard(user):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


def require_tenant():
    def checker(current_user=Depends(get_current_user)):
        if not current_user.get("tenant_id"):
            raise HTTPException(403, "Tenant context required")
        return current_user

    return checker
