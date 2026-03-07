from bson import ObjectId
from datetime import datetime
from app.db.database import db
from app.crud.users import serialize_user


def serialize_superadmin(user_doc):
    return {
        "id": str(user_doc["_id"]),
        "userId": str(user_doc["_id"]),
        "user": serialize_user(user_doc),  # attach user details
        "createdAt": user_doc["createdAt"],
        "updatedAt": user_doc["updatedAt"],
    }


async def get_superadmin_by_user(user_id: str):
    user = await db.users.find_one(
        {"_id": ObjectId(user_id), "role": {"$in": ["super_admin", "super-admin"]}}
    )
    if not user:
        return None
    return serialize_superadmin(user)


ROLE_VALUES = ["super_admin", "super-admin"]


async def update_superadmin(user_id: str, updates: dict):
    allowed_fields = ["fullName", "profileImageURL", "contactNo", "country", "status"]
    user_fields = {k: v for k, v in updates.items() if k in allowed_fields}

    if user_fields:
        user_fields["updatedAt"] = datetime.utcnow()
        user_fields["role"] = "super_admin"
        result = await db.users.update_one(
            {"_id": ObjectId(user_id), "role": {"$in": ROLE_VALUES}},
            {"$set": user_fields},
        )
        # Optional: check matched_count
        if result.matched_count == 0:
            return None

    # Fetch the updated document
    user = await db.users.find_one(
        {"_id": ObjectId(user_id), "role": {"$in": ROLE_VALUES}}
    )
    if not user:
        return None

    return serialize_superadmin(user)
