from datetime import datetime

from bson import ObjectId
from fastapi import HTTPException, status

from app.crud.users import create_user
from app.db.database import db, student_performance_collection
from app.utils.limits import check_tenant_limits


def _extract_tenant_id(payload: dict, *, required: bool = True) -> str | None:
    tenant_id = payload.get("tenantId")
    if not tenant_id:
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenantId is required and must be a valid ObjectId",
            )
        return None
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenantId is required and must be a valid ObjectId",
        )
    return tenant_id


async def _ensure_tenant_exists(tenant_id: str | None):
    if not tenant_id:
        return
    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant not found: {tenant_id}",
        )


async def _rollback_user_tree(
    user_id: ObjectId,
    *,
    student_id: ObjectId | None = None,
    teacher_id: ObjectId | None = None,
):
    if student_id:
        await db.students.delete_one({"_id": student_id})
        await student_performance_collection.delete_many({"studentId": student_id})
    if teacher_id:
        await db.teachers.delete_one({"_id": teacher_id})
    await db.users.delete_one({"_id": user_id})


async def signup_student(payload: dict) -> dict:
    # Students are global accounts by default; tenant context is optional.
    tenant_id = _extract_tenant_id(payload, required=False)
    await _ensure_tenant_exists(tenant_id)
    
    if tenant_id:
        await check_tenant_limits(tenant_id, "students")

    user_payload = {
        "fullName": payload.get("fullName"),
        "email": payload.get("email"),
        "password": payload.get("password"),
        "role": "student",
        "status": payload.get("status", "active"),
        "profileImageURL": payload.get("profileImageURL"),
        "contactNo": payload.get("contactNo"),
        "country": payload.get("country"),
    }
    if tenant_id:
        user_payload["tenantId"] = tenant_id

    try:
        user = await create_user(user_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user_oid = ObjectId(user["id"])
    student_id = None
    try:
        student_doc = {
            "userId": user_oid,
            "enrolledCourses": [],
            "completedCourses": [],
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        if tenant_id:
            student_doc["tenantId"] = ObjectId(tenant_id)
        student_result = await db.students.insert_one(student_doc)
        student_id = student_result.inserted_id

        perf_doc = {
            "studentId": student_id,
            "userId": user_oid,
            "studentName": user["fullName"],
            "totalPoints": 0,
            "pointsThisWeek": 0,
            "xp": 0,
            "level": 1,
            "xpToNextLevel": 300,
            "badges": [],
            "certificates": [],
            "weeklyStudyTime": [],
            "courseStats": [],
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        if tenant_id:
            perf_doc["tenantId"] = ObjectId(tenant_id)

        await student_performance_collection.insert_one(perf_doc)
    except HTTPException:
        await _rollback_user_tree(user_oid, student_id=student_id)
        raise
    except Exception:
        await _rollback_user_tree(user_oid, student_id=student_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to complete student registration right now. Please try again.",
        )

    user["studentId"] = str(student_id)
    return user


async def signup_teacher(payload: dict) -> dict:
    tenant_id = _extract_tenant_id(payload)
    await _ensure_tenant_exists(tenant_id)
    
    await check_tenant_limits(tenant_id, "teachers")

    user_payload = {
        "fullName": payload.get("fullName"),
        "email": payload.get("email"),
        "password": payload.get("password"),
        "role": "teacher",
        "status": payload.get("status", "active"),
        "profileImageURL": payload.get("profileImageURL"),
        "contactNo": payload.get("contactNo"),
        "country": payload.get("country"),
        "tenantId": tenant_id,
    }

    try:
        user = await create_user(user_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user_oid = ObjectId(user["id"])
    teacher_id = None
    try:
        teacher_doc = {
            "userId": user_oid,
            "tenantId": ObjectId(tenant_id),
            "assignedCourses": [],
            "qualifications": payload.get("qualifications", []),
            "subjects": payload.get("subjects", []),
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        teacher_result = await db.teachers.insert_one(teacher_doc)
        teacher_id = teacher_result.inserted_id
    except HTTPException:
        await _rollback_user_tree(user_oid, teacher_id=teacher_id)
        raise
    except Exception:
        await _rollback_user_tree(user_oid, teacher_id=teacher_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to complete teacher registration right now. Please try again.",
        )

    user["teacherId"] = str(teacher_id)
    return user
