from bson import ObjectId
from datetime import datetime
from app.db.database import db
from app.utils.security import hash_password, verify_password
from app.utils.user_status import is_auth_status_allowed

ROLE_ALIASES = {
    "super-admin": "super_admin",
    "super_admin": "super_admin",
}

ALLOWED_USER_FIELDS = {
    "fullName",
    "email",
    "password",
    "role",
    "status",
    "profileImageURL",
    "contactNo",
    "country",
    "tenantId",
}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role, role)


def serialize_user(u: dict):
    return {
        "id": str(u["_id"]),
        "fullName": u["fullName"],
        "email": u["email"],
        "profileImageURL": u.get("profileImageURL"),
        "contactNo": u.get("contactNo"),
        "country": u.get("country"),
        "role": normalize_role(u["role"]),
        "status": u["status"],
        "tenantId": str(u["tenantId"]) if u.get("tenantId") else None,
        "studentId": u.get("studentId"),
        "teacherId": u.get("teacherId"),
        "adminId": u.get("adminId"),
        "createdAt": u.get("createdAt"),
        "updatedAt": u["updatedAt"],
        "lastLogin": u.get("lastLogin"),
    }


async def get_user_by_email(email: str):
    return await db.users.find_one({"email": email.lower()})


async def create_user(data: dict):
    user_doc = {k: v for k, v in data.items() if k in ALLOWED_USER_FIELDS and v is not None}

    if not user_doc.get("fullName"):
        raise ValueError("fullName is required")
    if not user_doc.get("email"):
        raise ValueError("email is required")
    if not user_doc.get("password"):
        raise ValueError("password is required")
    if not user_doc.get("role"):
        raise ValueError("role is required")

    user_doc["email"] = user_doc["email"].lower()
    user_doc["role"] = normalize_role(user_doc["role"])
    user_doc.setdefault("status", "active")
    user_doc["password"] = hash_password(user_doc["password"])
    user_doc["createdAt"] = datetime.utcnow()
    user_doc["updatedAt"] = datetime.utcnow()
    user_doc["lastLogin"] = None

    if await db.users.find_one({"email": user_doc["email"]}):
        raise ValueError("Email already registered")

    if user_doc.get("tenantId"):
        user_doc["tenantId"] = ObjectId(user_doc["tenantId"])

    result = await db.users.insert_one(user_doc)
    new_user = await db.users.find_one({"_id": result.inserted_id})
    return serialize_user(new_user)


async def verify_user(email: str, password: str):
    u = await get_user_by_email(email)
    if not u:
        return None
    try:
        password_ok = verify_password(password, u["password"])
    except Exception:
        # Legacy fallback: if plaintext password was stored, upgrade it to bcrypt on login.
        if u.get("password") == password:
            new_hash = hash_password(password)
            await db.users.update_one(
                {"_id": u["_id"]},
                {"$set": {"password": new_hash, "updatedAt": datetime.utcnow()}},
            )
            u["password"] = new_hash
            password_ok = True
        else:
            return None
    if not password_ok:
        return None
    if not is_auth_status_allowed(u.get("status")):
        return None

    # After verifying, fetch the role-specific doc to get the tenant_id and role-specific ID
    user_id = u["_id"]
    role = normalize_role(u["role"])
    u["role"] = role
    tenant_id = u.get("tenantId")  # Start with tenantId from users collection
    role_doc = None

    if role == "teacher":
        role_doc = await db.teachers.find_one(
            {"userId": user_id, "tenantId": u.get("tenantId")}
            if u.get("tenantId")
            else {"userId": user_id}
        )
        if not role_doc:
            role_doc = await db.teachers.find_one(
                {"userId": user_id},
                sort=[("updatedAt", -1), ("_id", -1)],
            )
    elif role == "student":
        role_doc = await db.students.find_one({"userId": user_id})
    elif role == "admin":
        role_doc = await db.admins.find_one({"userId": user_id})

    # If the role-specific document has a tenantId, it takes precedence
    if role_doc and role_doc.get("tenantId"):
        tenant_id = role_doc.get("tenantId")

    # Add tenantId and role-specific ID to the user object before serializing
    u["tenantId"] = tenant_id
    if role == "teacher" and role_doc:
        u["teacherId"] = str(role_doc["_id"])
    elif role == "student" and role_doc:
        u["studentId"] = str(role_doc["_id"])
    elif role == "admin" and role_doc:
        u["adminId"] = str(role_doc["_id"])

    return serialize_user(u)


async def update_last_login(user_id: str):
    await db.users.update_one(
        {"_id": ObjectId(user_id)}, {"$set": {"lastLogin": datetime.utcnow()}}
    )
