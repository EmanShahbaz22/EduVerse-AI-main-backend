from bson import ObjectId
from datetime import datetime
from pymongo import ReturnDocument
from app.db.database import db
from app.schemas.courses import CourseCreate, CourseUpdate
from app.crud.courses.helpers import (
    get_collections,
    validate_id,
    clean_update_data,
    serialize_course,
)
from app.utils.limits import check_tenant_limits

courses_col, students_col, _ = get_collections()


async def create_course(course_data: CourseCreate) -> dict:
    d = course_data.dict()

    if not d.get("tenantId") or not ObjectId.is_valid(d["tenantId"]):
        raise ValueError(f"Invalid tenant ID: {d.get('tenantId', 'N/A')}")
    if not d.get("teacherId") or not ObjectId.is_valid(d["teacherId"]):
        raise ValueError(f"Invalid teacher ID: {d.get('teacherId', 'N/A')}")

    tenant_id = ObjectId(d["tenantId"])
    teacher_id = ObjectId(d["teacherId"])

    if not await db.tenants.find_one({"_id": tenant_id}):
        raise ValueError(f"Tenant not found: {d['tenantId']}")

    teacher = await db.teachers.find_one({"_id": teacher_id, "tenantId": tenant_id})
    if not teacher:
        if await db.teachers.find_one({"_id": teacher_id}):
            raise ValueError("Teacher belongs to different tenant")
        raise ValueError(f"Teacher not found: {d['teacherId']}")

    # Enforce Subscription Constraints
    await check_tenant_limits(tenant_id, "courses")

    d["tenantId"], d["teacherId"] = tenant_id, teacher_id
    d["createdAt"] = d["updatedAt"] = datetime.utcnow()
    d["enrolledStudents"] = 0

    result = await courses_col.insert_one(d)
    await db.teachers.update_one(
        {"_id": teacher_id},
        {
            "$addToSet": {"assignedCourses": result.inserted_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )

    d["_id"] = str(result.inserted_id)
    d["tenantId"] = str(tenant_id)
    d["teacherId"] = str(teacher_id)
    return d


async def update_course(course_id: str, tenant_id: str, course_update: CourseUpdate):
    if not ObjectId.is_valid(course_id) or not ObjectId.is_valid(tenant_id):
        return None

    existing = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not existing:
        return None
    old_teacher = existing.get("teacherId")

    cleaned = await clean_update_data(course_update.dict(exclude_unset=True))
    if not cleaned:
        return serialize_course({**existing})

    cleaned["updatedAt"] = datetime.utcnow()
    if "tenantId" in cleaned and isinstance(cleaned["tenantId"], str):
        cleaned["tenantId"] = ObjectId(cleaned["tenantId"])
    if "teacherId" in cleaned and isinstance(cleaned["teacherId"], str):
        cleaned["teacherId"] = ObjectId(cleaned["teacherId"])

    result = await courses_col.find_one_and_update(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)},
        {"$set": cleaned},
        return_document=ReturnDocument.AFTER,
    )

    if result:
        new_teacher = cleaned.get("teacherId")
        if new_teacher and str(old_teacher) != str(new_teacher):
            if old_teacher:
                await db.teachers.update_one(
                    {"_id": old_teacher},
                    {"$pull": {"assignedCourses": ObjectId(course_id)}},
                )
            await db.teachers.update_one(
                {"_id": new_teacher},
                {"$addToSet": {"assignedCourses": ObjectId(course_id)}},
            )
        return serialize_course(result)
    return None


async def delete_course(course_id: str, tenant_id: str) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    coid, toid = ObjectId(course_id), ObjectId(tenant_id)
    course = await courses_col.find_one({"_id": coid, "tenantId": toid})
    if not course:
        if await courses_col.find_one({"_id": coid}):
            return {"success": False, "message": "Course belongs to different tenant"}
        return {"success": False, "message": f"Course not found: {course_id}"}

    teacher_id = course.get("teacherId")
    res = await courses_col.delete_one({"_id": coid, "tenantId": toid})

    if res.deleted_count > 0:
        if teacher_id:
            tid = ObjectId(teacher_id) if isinstance(teacher_id, str) else teacher_id
            await db.teachers.update_one(
                {"_id": tid},
                {
                    "$pull": {"assignedCourses": coid},
                    "$set": {"updatedAt": datetime.utcnow()},
                },
            )
        await students_col.update_many(
            {"enrolledCourses": course_id},
            {
                "$pull": {"enrolledCourses": course_id},
                "$set": {"updatedAt": datetime.utcnow()},
            },
        )
        return {"success": True, "message": "Course deleted"}
    return {"success": False, "message": "Failed to delete course"}


async def publish_course(course_id: str, tenant_id: str, publish: bool = True) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    new_status = "published" if publish else "draft"
    if course.get("status", "draft") == new_status:
        return {
            "success": True,
            "message": f"Already {new_status}",
            "course": serialize_course(course),
        }

    res = await courses_col.update_one(
        {"_id": ObjectId(course_id)},
        {
            "$set": {
                "status": new_status,
                "updatedAt": datetime.now(),
                "publishedAt": datetime.now() if publish else None,
            }
        },
    )
    if res.modified_count == 0:
        return {"success": False, "message": "Failed to update status"}

    updated = await courses_col.find_one({"_id": ObjectId(course_id)})
    return {
        "success": True,
        "message": f"{'Published' if publish else 'Unpublished'}",
        "course": serialize_course(updated),
    }
