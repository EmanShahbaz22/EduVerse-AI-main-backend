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

async def get_student_by_email(email: str):
    user = await users_collection.find_one({"email": email, "role": "student"})
    if not user:
        return None
    student = await COLLECTION.find_one({"userId": user["_id"]})
    return merge_user_data(student, user) if student else None
async def get_student_by_id(student_id: str, tenantId: str):
    student = await COLLECTION.find_one(
        {"_id": ObjectId(student_id)}
    )
    if not student:
        return None
    user = await users_collection.find_one({"_id": student.get("userId")})
    return merge_user_data(student, user)
async def list_students(tenantId: str = None):
    pipeline = []
    if tenantId:
        tenant_oid = ObjectId(tenantId) if isinstance(tenantId, str) and ObjectId.is_valid(tenantId) else tenantId
        tenant_courses = [doc["_id"] async for doc in courses_collection.find({"tenantId": tenant_oid}, {"_id": 1})]
        
        # Match students who are either natively part of this tenant OR enrolled in a tenant's course
        match_query = {
            "$or": [
                {"tenantId": tenant_oid},
                {"enrolledCourses": {"$in": [str(c) for c in tenant_courses] + tenant_courses}}
            ]
        }
        pipeline.append({"$match": match_query})
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
    student = await COLLECTION.find_one({"_id": ObjectId(student_id)})
    if not student:
        return False
        
    tenant_courses = [str(doc["_id"]) async for doc in courses_collection.find({"tenantId": ObjectId(tenant_id)}, {"_id": 1})]
    tenant_courses_oids = [ObjectId(cid) for cid in tenant_courses]
    courses_to_remove = [c for c in student.get("enrolledCourses", []) if str(c) in tenant_courses or c in tenant_courses_oids]
    
    if not courses_to_remove:
        return True # Nothing to remove
        
    for cid in courses_to_remove:
        cid_obj = ObjectId(cid) if isinstance(cid, str) and ObjectId.is_valid(cid) else cid
        await courses_collection.update_one({"_id": cid_obj}, {"$inc": {"enrolledStudents": -1}})
        
    await COLLECTION.update_one(
        {"_id": ObjectId(student_id)},
        {"$pull": {"enrolledCourses": {"$in": courses_to_remove}}}
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
    raise HTTPException(status_code=403, detail="Admins cannot update student profiles directly.")
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
