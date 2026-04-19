from datetime import datetime
import secrets
from bson import ObjectId
from fastapi import HTTPException
from app.db.database import db, users_collection
from app.schemas.teachers import TeacherCreate, TeacherUpdate
from app.utils.security import hash_password, verify_password
from app.utils.exceptions import not_found, bad_request
from app.crud.tenants import check_tenant_limit


def to_oid(id_str: str, field: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(400, f"Invalid {field}")


def _normalize_list(items, key=None):
    return [
        (i.get(key, "") if isinstance(i, dict) else str(i))
        if not isinstance(i, str)
        else i
        for i in items
    ]


def serialize_teacher(t: dict) -> dict:
    return {
        "id": str(t["_id"]),
        "fullName": t.get("fullName", ""),
        "email": t.get("email", ""),
        "profileImageURL": t.get("profileImageURL", ""),
        "assignedCourses": [str(c) for c in t.get("assignedCourses", [])],
        "contactNo": t.get("contactNo"),
        "country": t.get("country"),
        "status": t.get("status", "active"),
        "role": t.get("role", "teacher"),
        "qualifications": _normalize_list(t.get("qualifications", []), "degree"),
        "subjects": _normalize_list(t.get("subjects", []), "name"),
        "tenantId": str(t.get("tenantId", "")),
        "createdAt": t.get("createdAt"),
        "updatedAt": t.get("updatedAt"),
        "lastLogin": t.get("lastLogin"),
    }


def merge_user_data_teacher(teacher_doc, user_doc):
    if not teacher_doc:
        return None
    merged = {**teacher_doc}
    if user_doc:
        for k in (
            "fullName",
            "email",
            "profileImageURL",
            "contactNo",
            "country",
            "createdAt",
            "lastLogin",
        ):
            merged[k] = user_doc.get(k, merged.get(k, ""))
        merged["status"] = user_doc.get("status", "active")
        merged["role"] = user_doc.get("role", "teacher")
    return serialize_teacher(merged)


def _is_teacher_role(role: str | None) -> bool:
    return str(role or "").replace("-", "_").lower() == "teacher"


def _build_teacher_doc(
    *,
    user_id: ObjectId,
    tenant_id: str,
    assigned_courses: list | None = None,
    qualifications: list | None = None,
    subjects: list | None = None,
) -> dict:
    return {
        "userId": user_id,
        "tenantId": ObjectId(tenant_id),
        "assignedCourses": [
            ObjectId(c) if ObjectId.is_valid(c) else c for c in (assigned_courses or [])
        ],
        "qualifications": qualifications or [],
        "subjects": subjects or [],
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }


async def create_or_link_teacher_for_tenant(
    *,
    tenant_id: str,
    email: str,
    full_name: str | None = None,
    password: str | None = None,
    profile_image_url: str | None = "",
    contact_no: str | None = None,
    country: str | None = None,
    status: str = "active",
    qualifications: list | None = None,
    subjects: list | None = None,
    assigned_courses: list | None = None,
) -> tuple[dict | None, str, str | None]:
    """
    Create a new teacher account or link existing teacher user to this tenant.
    Returns: (teacher_response, mode, generated_password)
      mode: created | linked_existing | exists
    """
    # Enforce subscription limits
    await check_tenant_limit(tenant_id, "Teachers")

    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(404, f"Tenant not found: {tenant_id}")

    normalized_email = email.lower().strip()
    existing_user = await users_collection.find_one({"email": normalized_email})
    generated_password = None

    if existing_user and not _is_teacher_role(existing_user.get("role")):
        raise HTTPException(
            status_code=409,
            detail=f"Email {normalized_email} already belongs to a non-teacher account",
        )

    if not existing_user:
        if not password:
            generated_password = secrets.token_urlsafe(9)
            password = generated_password
        user_doc = {
            "fullName": (full_name or normalized_email.split("@")[0]).strip(),
            "email": normalized_email,
            "password": hash_password(password),
            "role": "teacher",
            "status": status or "active",
            "profileImageURL": profile_image_url or "",
            "contactNo": contact_no,
            "country": country,
            "tenantId": ObjectId(tenant_id),
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
            "lastLogin": None,
        }
        user_result = await users_collection.insert_one(user_doc)
        existing_user = {**user_doc, "_id": user_result.inserted_id}

    teacher_doc = await db.teachers.find_one(
        {"userId": existing_user["_id"], "tenantId": ObjectId(tenant_id)}
    )
    if teacher_doc:
        user = await users_collection.find_one({"_id": existing_user["_id"]})
        return merge_user_data_teacher(teacher_doc, user), "exists", None

    new_teacher_doc = _build_teacher_doc(
        user_id=existing_user["_id"],
        tenant_id=tenant_id,
        assigned_courses=assigned_courses,
        qualifications=qualifications,
        subjects=subjects,
    )
    result = await db.teachers.insert_one(new_teacher_doc)
    new_teacher_doc["_id"] = result.inserted_id

    user_updates = {"updatedAt": datetime.utcnow()}
    user_updates["tenantId"] = ObjectId(tenant_id)
    if full_name:
        user_updates["fullName"] = full_name
    if profile_image_url:
        user_updates["profileImageURL"] = profile_image_url
    if contact_no is not None:
        user_updates["contactNo"] = contact_no
    if country is not None:
        user_updates["country"] = country
    if status:
        user_updates["status"] = status
    await users_collection.update_one({"_id": existing_user["_id"]}, {"$set": user_updates})
    user = await users_collection.find_one({"_id": existing_user["_id"]})

    mode = "created" if generated_password else "linked_existing"
    return merge_user_data_teacher(new_teacher_doc, user), mode, generated_password


async def create_teacher(data: TeacherCreate):
    d = data.dict()
    teacher, mode, _ = await create_or_link_teacher_for_tenant(
        tenant_id=d["tenantId"],
        email=d["email"],
        full_name=d.get("fullName"),
        password=d.get("password"),
        profile_image_url=d.get("profileImageURL", ""),
        contact_no=d.get("contactNo"),
        country=d.get("country"),
        status=d.get("status", "active"),
        qualifications=d.get("qualifications", []),
        subjects=d.get("subjects", []),
        assigned_courses=d.get("assignedCourses", []),
    )
    if mode == "exists":
        raise HTTPException(
            status_code=409, detail="Teacher is already added to this tenant"
        )
    return teacher


async def get_all_teachers(tenant_id: str = None):
    pipeline = []
    if tenant_id and ObjectId.is_valid(tenant_id):
        pipeline.append({"$match": {"tenantId": ObjectId(tenant_id)}})
    pipeline.extend(
        [
            {
                "$lookup": {
                    "from": "users",
                    "localField": "userId",
                    "foreignField": "_id",
                    "as": "userDetails",
                }
            },
            {"$unwind": {"path": "$userDetails", "preserveNullAndEmptyArrays": True}},
        ]
    )
    results = []
    async for doc in db.teachers.aggregate(pipeline):
        user_info = doc.pop("userDetails", {}) or {}
        results.append(merge_user_data_teacher(doc, user_info))
    return results


async def get_teacher(id: str):
    teacher = await db.teachers.find_one({"_id": to_oid(id, "teacherId")})
    if not teacher:
        return None
    user = await users_collection.find_one({"_id": teacher.get("userId")})
    return merge_user_data_teacher(teacher, user)


def _clean_updates(updates: dict) -> dict:
    return {
        k: v
        for k, v in updates.items()
        if v is not None
        and not (isinstance(v, str) and v.strip() == "" and k != "profileImageURL")
    }


USER_FIELDS = {"fullName", "email", "profileImageURL", "contactNo", "country", "status"}


async def update_teacher(id: str, updates: dict):
    teacher = await db.teachers.find_one({"_id": to_oid(id, "teacherId")})
    if not teacher:
        return None

    cleaned = _clean_updates(updates)
    if not cleaned:
        return await get_teacher(id)

    cleaned["updatedAt"] = datetime.utcnow()
    user_updates = {k: v for k, v in cleaned.items() if k in USER_FIELDS}
    teacher_updates = {
        k: v for k, v in cleaned.items() if k not in USER_FIELDS and k != "updatedAt"
    }

    if user_updates and teacher.get("userId"):
        user_updates["updatedAt"] = datetime.utcnow()
        await db.users.update_one({"_id": teacher["userId"]}, {"$set": user_updates})

    if teacher_updates:
        if "tenantId" in teacher_updates:
            teacher_updates["tenantId"] = ObjectId(teacher_updates["tenantId"])
        if "assignedCourses" in teacher_updates:
            teacher_updates["assignedCourses"] = [
                ObjectId(c) if ObjectId.is_valid(c) else c
                for c in teacher_updates["assignedCourses"]
            ]
        teacher_updates["updatedAt"] = datetime.utcnow()
        await db.teachers.update_one(
            {"_id": to_oid(id, "teacherId")}, {"$set": teacher_updates}
        )

    return await get_teacher(id)


async def delete_teacher(id: str):
    teacher = await db.teachers.find_one({"_id": to_oid(id, "teacherId")})
    if not teacher:
        return False
    result = await db.teachers.delete_one({"_id": to_oid(id, "teacherId")})
    if teacher.get("userId"):
        uid = (
            teacher["userId"]
            if isinstance(teacher["userId"], ObjectId)
            else ObjectId(teacher["userId"])
        )
        # Keep account if this teacher user is linked to any other tenant.
        remaining_links = await db.teachers.count_documents({"userId": uid})
        if remaining_links == 0:
            await users_collection.delete_one({"_id": uid})
    return result.deleted_count > 0


async def change_password(id: str, old_password: str, new_password: str):
    teacher = await db.teachers.find_one({"_id": to_oid(id, "teacherId")})
    if not teacher or not teacher.get("userId"):
        return None
    user = await users_collection.find_one({"_id": teacher["userId"]})
    if not user:
        return None
    if not verify_password(old_password, user.get("password", "")):
        return "INCORRECT"
    await users_collection.update_one(
        {"_id": teacher["userId"]},
        {
            "$set": {
                "password": hash_password(new_password),
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    return True
