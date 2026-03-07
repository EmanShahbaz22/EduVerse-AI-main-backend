from datetime import datetime
from bson import ObjectId
from fastapi import HTTPException
from app.schemas.students import StudentCreate, StudentUpdate
from app.utils.mongo import fix_object_ids
from app.utils.security import hash_password, verify_password
from app.utils.exceptions import not_found, bad_request
from app.db.database import (
    students_collection as COLLECTION, courses_collection, users_collection, db, student_performance_collection,
)
def merge_user_data(student_doc, user_doc):
    if not student_doc:
        return None
    merged = {**student_doc}
    if user_doc:
        for k in (
            "fullName", "email", "password", "role", "status", "createdAt", "updatedAt", "lastLogin", "profileImageURL", ):
            merged[k] = user_doc.get(k, merged.get(k, ""))
        merged.setdefault("role", "student")
        merged.setdefault("status", "active")
    merged = fix_object_ids(merged)
    if "_id" in merged:
        merged["id"] = str(merged.pop("_id"))
    return merged
async def create_student(student: StudentCreate, tenant_id: str):
    data = student.dict()
    if await users_collection.find_one({"email": data["email"]}):
        raise HTTPException(400, "Email already exists")
    if not await db.tenants.find_one({"_id": ObjectId(tenant_id)}):
        raise HTTPException(404, f"Tenant not found: {tenant_id}")
    user_doc = {"fullName": data["fullName"], "email": data["email"].lower(), "password": hash_password(data["password"]), "role": "student", "status": data.get("status", "active"), "profileImageURL": data.get("profileImageURL", ""), "contactNo": data.get("contactNo"), "country": data.get("country"), "tenantId": ObjectId(data.get("tenantId") or tenant_id), "createdAt": datetime.utcnow(), "updatedAt": datetime.utcnow(), "lastLogin": None, }
    user_id = (await users_collection.insert_one(user_doc)).inserted_id
    student_doc = {"userId": user_id, "tenantId": ObjectId(tenant_id), "enrolledCourses": [], "completedCourses": [], "createdAt": datetime.utcnow(), "updatedAt": datetime.utcnow(), }
    student_id = None
    try:
        result = await COLLECTION.insert_one(student_doc)
        student_id = result.inserted_id
        await student_performance_collection.insert_one(
            {"tenantId": ObjectId(tenant_id), "studentId": student_id, "userId": user_id, "studentName": data["fullName"], "totalPoints": 0, "pointsThisWeek": 0, "xp": 0, "level": 1, "xpToNextLevel": 300, "badges": [], "certificates": [], "weeklyStudyTime": [], "courseStats": [], "createdAt": datetime.utcnow(), "updatedAt": datetime.utcnow(), }
        )
    except Exception as exc:
        if student_id:
            await COLLECTION.delete_one({"_id": student_id})
            await student_performance_collection.delete_many({"studentId": student_id})
        await users_collection.delete_one({"_id": user_id})
        raise HTTPException(500, f"Failed to create student profile: {exc}")
    return fix_object_ids(
        {"_id": student_id, "tenantId": ObjectId(tenant_id), **{k: user_doc[k]
                for k in (
                    "fullName", "email", "password", "profileImageURL", "contactNo", "country", "status", "role", "createdAt", "updatedAt", "lastLogin", )}, "enrolledCourses": [], "completedCourses": [], }
    )
async def get_student_by_email(email: str):
    user = await users_collection.find_one({"email": email, "role": "student"})
    if not user:
        return None
    student = await COLLECTION.find_one({"userId": user["_id"]})
    return merge_user_data(student, user) if student else None
async def get_student_by_id(student_id: str, tenantId: str):
    student = await COLLECTION.find_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenantId)}
    )
    if not student:
        return None
    user = await users_collection.find_one({"_id": student.get("userId")})
    return merge_user_data(student, user)
async def list_students(tenantId: str = None):
    pipeline = [{"$match": {"tenantId": ObjectId(tenantId)}}] if tenantId else []
    pipeline.extend(
        [
            {"$lookup": {"from": "users", "localField": "userId", "foreignField": "_id", "as": "userDetails", }}, {"$unwind": {"path": "$userDetails", "preserveNullAndEmptyArrays": True}}, ]
    )
    results = []
    async for doc in COLLECTION.aggregate(pipeline):
        ui = doc.pop("userDetails", {}) or {}
        results.append(
            fix_object_ids(
                {"_id": doc["_id"], "tenantId": doc.get("tenantId"), "fullName": ui.get("fullName", ""), "email": ui.get("email", ""), "role": ui.get("role", "student"), "status": ui.get("status", "active"), "profileImageURL": ui.get("profileImageURL", ""), "contactNo": ui.get("contactNo"), "country": ui.get("country"), "enrolledCourses": doc.get("enrolledCourses", []), "completedCourses": doc.get("completedCourses", []), "createdAt": ui.get("createdAt"), "updatedAt": ui.get("updatedAt"), "lastLogin": ui.get("lastLogin"), }
            )
        )
    return results
async def delete_student(student_id: str, tenant_id: str):
    student = await COLLECTION.find_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}
    )
    if not student:
        return False
    for cid in student.get("enrolledCourses", []):
        if ObjectId.is_valid(cid):
            await courses_collection.update_one(
                {"_id": ObjectId(cid)}, {"$inc": {"enrolledStudents": -1}}
            )
    res = await COLLECTION.delete_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}
    )
    if res.deleted_count == 0:
        return False
    if student.get("userId"):
        uid = (
            student["userId"]
            if isinstance(student["userId"], ObjectId)
            else ObjectId(student["userId"])
        )
        await users_collection.delete_one({"_id": uid})
    await student_performance_collection.delete_one(
        {"studentId": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}
    )
    return True
async def get_student_by_user(user_id: str):
    uid = ObjectId(user_id) if isinstance(user_id, str) else user_id
    student = await COLLECTION.find_one({"userId": uid})
    return (
        merge_user_data(student, await users_collection.find_one({"_id": uid}))
        if student
        else None
    )
async def get_student_me(current_user: dict):
    student = await COLLECTION.find_one({"userId": ObjectId(current_user["user_id"])})
    if not student:
        not_found("Student profile")
    return merge_user_data(
        student, await users_collection.find_one({"_id": student["userId"]})
    )
async def update_student_me(current_user: dict, data: StudentUpdate):
    student = await COLLECTION.find_one({"userId": ObjectId(current_user["user_id"])})
    if not student:
        not_found("Student profile")
    tenant_id = student.get("tenantId")
    if tenant_id:
        return await update_student(str(student["_id"]), str(tenant_id), data)

    # Tenant-independent student profile update path.
    update_data = {
        k: v
        for k, v in data.dict(exclude_unset=True).items()
        if v is not None
        and not (isinstance(v, str) and v.strip() == "" and k != "profileImageURL")
    }
    if not update_data:
        return merge_user_data(
            student, await users_collection.find_one({"_id": student["userId"]})
        )

    if "email" in update_data:
        update_data["email"] = update_data["email"].lower()
    update_data["updatedAt"] = datetime.utcnow()

    await db.users.update_one({"_id": student["userId"]}, {"$set": update_data})
    await COLLECTION.update_one(
        {"_id": ObjectId(student["_id"])}, {"$set": {"updatedAt": datetime.utcnow()}}
    )

    refreshed = await COLLECTION.find_one({"_id": ObjectId(student["_id"])})
    user = await users_collection.find_one({"_id": student["userId"]})
    return merge_user_data(refreshed, user)
async def update_student(student_id: str, tenantId: str, update: StudentUpdate):
    if not ObjectId.is_valid(student_id) or not ObjectId.is_valid(tenantId):
        return None
    student = await COLLECTION.find_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenantId)}
    )
    if not student or not student.get("userId"):
        return None
    update_data = {k: v
        for k, v in update.dict(exclude_unset=True).items()
        if v is not None
        and not (isinstance(v, str) and v.strip() == "" and k != "profileImageURL")}
    if not update_data:
        return await get_student_by_id(student_id, tenantId)
    if "email" in update_data:
        update_data["email"] = update_data["email"].lower()
    update_data["updatedAt"] = datetime.utcnow()
    await db.users.update_one({"_id": student["userId"]}, {"$set": update_data})
    await COLLECTION.update_one(
        {"_id": ObjectId(student_id)}, {"$set": {"updatedAt": datetime.utcnow()}}
    )
    if "fullName" in update_data:
        await student_performance_collection.update_one(
            {"studentId": ObjectId(student_id), "tenantId": ObjectId(tenantId)}, {"$set": {"studentName": update_data["fullName"], "updatedAt": datetime.utcnow(), }}, )
    return await get_student_by_id(student_id, tenantId)
async def change_student_me_password(
    current_user: dict, old_password: str, new_password: str
):
    user = await users_collection.find_one({"_id": ObjectId(current_user["user_id"])})
    if not user:
        not_found("User")
    if not verify_password(old_password, user["password"]):
        bad_request("Old password is incorrect")
    if verify_password(new_password, user["password"]):
        bad_request("New password must differ from old")
    await users_collection.update_one(
        {"_id": user["_id"]}, {"$set": {"password": hash_password(new_password), "updatedAt": datetime.utcnow(), }}, )
    return {"message": "Password updated", "updatedAt": datetime.utcnow()}
