from bson import ObjectId
from datetime import datetime
from app.db.database import db, users_collection
from app.schemas.admins import AdminCreate, AdminUpdateProfile
from app.utils.exceptions import not_found, bad_request
from app.utils.security import hash_password, verify_password


def serialize_admin(admin: dict) -> dict:
    return {
        "id": str(admin["_id"]),
        "fullName": admin.get("fullName", ""),
        "email": admin.get("email", ""),
        "country": admin.get("country"),
        "contactNo": admin.get("contactNo"),
        "profileImageURL": admin.get("profileImageURL", ""),
        "status": admin.get("status", "active"),
        "role": admin.get("role", "admin"),
        "createdAt": admin.get("createdAt"),
        "updatedAt": admin.get("updatedAt"),
    }


def merge_user_data_admin(admin_doc, user_doc):
    if not admin_doc:
        return None
    merged = {**admin_doc}
    if user_doc:
        for k in (
            "fullName",
            "email",
            "profileImageURL",
            "contactNo",
            "country",
            "createdAt",
        ):
            merged[k] = user_doc.get(k, merged.get(k, ""))
        merged["status"] = user_doc.get("status", "active")
        merged["role"] = user_doc.get("role", "admin")
    return serialize_admin(merged)


def serialize_teacher(teacher: dict) -> dict:
    return {
        "id": str(teacher["_id"]),
        "fullName": teacher.get("fullName", ""),
        "email": teacher.get("email", ""),
        "profileImageURL": teacher.get("profileImageURL", ""),
        "assignedCourses": [str(c) for c in teacher.get("assignedCourses", [])],
        "contactNo": teacher.get("contactNo", ""),
        "country": teacher.get("country", ""),
        "status": "Active"
        if str(teacher.get("status", "")).lower() == "active"
        else "Inactive",
        "role": teacher.get("role", "teacher"),
        "qualifications": teacher.get("qualifications", []),
        "subjects": teacher.get("subjects", []),
        "tenantId": teacher.get("tenantId", ""),
        "createdAt": teacher.get("createdAt"),
        "updatedAt": teacher.get("updatedAt"),
        "lastLogin": teacher.get("lastLogin"),
    }


def serialize_student(student: dict) -> dict:
    return {
        "id": str(student["_id"]),
        "name": student.get("fullName", ""),
        "email": student.get("email", ""),
        "class": student.get("className"),
        "rollNo": student.get("rollNo"),
        "status": student.get("status", "Inactive"),
    }


def serialize_course(course: dict, teacher_name: str = "") -> dict:
    return {
        "id": str(course["_id"]),
        "title": course.get("title", ""),
        "code": course.get("courseCode", ""),
        "instructor": teacher_name,
        "status": course.get("status", "Inactive"),
    }


def clean_update_data(data: dict) -> dict:
    update_data = {k: v for k, v in data.items() if v is not None}
    if update_data:
        update_data["updatedAt"] = datetime.utcnow()
    return update_data


async def get_admin_by_email(email: str):
    user = await users_collection.find_one({"email": email, "role": "admin"})
    if not user:
        return None
    admin = await db.admins.find_one({"userId": user["_id"]})
    return merge_user_data_admin(admin, user)


async def create_admin(admin: AdminCreate):
    if await users_collection.find_one({"email": admin.email}):
        raise ValueError("Email already registered")
    if admin.password != admin.confirmPassword:
        raise ValueError("Passwords do not match")

    user_doc = {
        "fullName": f"{admin.firstName} {admin.lastName}",
        "email": admin.email,
        "password": hash_password(admin.password),
        "role": "admin",
        "status": "active",
        "profileImageURL": "",
        "contactNo": admin.phone,
        "country": admin.country,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
        "lastLogin": None,
    }
    user_result = await users_collection.insert_one(user_doc)

    admin_doc = {
        "userId": user_result.inserted_id,
        "tenantId": user_doc.get("tenantId"),
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    await db.admins.insert_one(admin_doc)
    return merge_user_data_admin(admin_doc, user_doc)


async def create_admin_profile(user_id: str, tenant_id: str = None):
    admin_doc = {
        "userId": ObjectId(user_id) if isinstance(user_id, str) else user_id,
        "tenantId": ObjectId(tenant_id)
        if tenant_id and isinstance(tenant_id, str)
        else tenant_id,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    await db.admins.insert_one(admin_doc)
    return admin_doc


async def get_all_courses(tenant_id: str = None):
    courses = []
    query = {}
    if tenant_id and ObjectId.is_valid(tenant_id):
        query["tenantId"] = ObjectId(tenant_id)
    async for course in db.courses.find(query):
        teacher_name = ""
        try:
            teacher_doc = await db.teachers.find_one(
                {"_id": ObjectId(course.get("teacherId")), **query}
            )
            if teacher_doc:
                teacher_name = teacher_doc.get("fullName", "")
        except Exception:
            pass
        courses.append(serialize_course(course, teacher_name))
    return courses


async def get_all_teachers(tenant_id: str = None):
    from app.crud.teachers import get_all_teachers as fetch_all

    return await fetch_all(tenant_id)


async def get_all_students(tenant_id: str = None):
    from app.crud.students import list_students as fetch_all

    return [
        {
            "id": str(s["_id"]),
            "name": s.get("fullName", ""),
            "email": s.get("email", ""),
            "class": s.get("className"),
            "rollNo": s.get("rollNo"),
            "status": s.get("status", "Inactive"),
        }
        for s in await fetch_all(tenant_id)
    ]


async def get_admin_me(current_user: dict):
    admin = await db.admins.find_one({"userId": ObjectId(current_user["user_id"])})
    if not admin:
        not_found("Admin profile")
    user = await users_collection.find_one({"_id": admin["userId"]})
    return merge_user_data_admin(admin, user)


async def update_admin_me(current_user: dict, data: AdminUpdateProfile):
    admin = await db.admins.find_one({"userId": ObjectId(current_user["user_id"])})
    if not admin:
        not_found("Admin profile")
    return await update_admin_profile(str(admin["_id"]), data)


async def update_admin_profile(admin_id: str, data: AdminUpdateProfile):
    admin = await db.admins.find_one({"_id": ObjectId(admin_id)})
    if not admin:
        return None
    user_id = admin.get("userId")
    update_data = clean_update_data(data.dict())
    if not update_data:
        return merge_user_data_admin(
            admin, await users_collection.find_one({"_id": user_id})
        )
    if user_id:
        await users_collection.update_one({"_id": user_id}, {"$set": update_data})
    
    await db.admins.update_one(
        {"_id": ObjectId(admin_id)}, {"$set": {"updatedAt": datetime.utcnow()}}
    )
    
    # Sync contact updates to the managed Tenant Document
    tenant_updates = {}
    if "contactNo" in update_data:
        tenant_updates["contactNumber"] = update_data["contactNo"]
    if "country" in update_data:
        tenant_updates["address"] = update_data["country"]
        
    if tenant_updates and admin.get("tenantId"):
        await db.tenants.update_one(
            {"_id": ObjectId(admin["tenantId"])},
            {"$set": tenant_updates}
        )
        
    return merge_user_data_admin(
        await db.admins.find_one({"_id": ObjectId(admin_id)}),
        await users_collection.find_one({"_id": user_id}),
    )


async def change_admin_me_password(
    current_user: dict, old_password: str, new_password: str
):
    user = await users_collection.find_one(
        {"_id": ObjectId(current_user["user_id"]), "role": "admin"}
    )
    if not user:
        not_found("User")
    if not verify_password(old_password, user["password"]):
        bad_request("Old password is incorrect")
    if verify_password(new_password, user["password"]):
        bad_request("New password must differ from old")
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password": hash_password(new_password),
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    await db.admins.update_one(
        {"userId": user["_id"]}, {"$set": {"updatedAt": datetime.utcnow()}}
    )
    return {"message": "Password updated", "updatedAt": datetime.utcnow()}
