from fastapi import HTTPException
from app.crud.users import create_user, verify_user, get_user_by_email
from app.crud.users import update_last_login
from app.utils.security import create_access_token
from app.utils.security import verify_password
from app.utils.user_status import is_auth_status_allowed


async def register_user(data):
    user = await create_user(data)
    return user


async def login_user(email: str, password: str):
    user = await verify_user(email, password)
    if not user:
        existing = await get_user_by_email(email)
        password_matches = False
        if existing and existing.get("password"):
            try:
                password_matches = verify_password(password, existing["password"])
            except Exception:
                password_matches = False
        if (
            existing
            and password_matches
            and not is_auth_status_allowed(existing.get("status"))
        ):
            raise HTTPException(
                status_code=403,
                detail="Your account is inactive. Please contact support.",
            )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await update_last_login(user["id"])

    token_tenant_id = None if user["role"] == "student" else user["tenantId"]

    token = create_access_token(
        {
            "user_id": user["id"],
            "role": user["role"],
            "tenant_id": token_tenant_id,
            "student_id": user.get("studentId"),
            "teacher_id": user.get("teacherId"),
            "admin_id": user.get("adminId"),
            "full_name": user.get("fullName"),
        }
    )

    return {"access_token": token, "token_type": "bearer", "user": user}
