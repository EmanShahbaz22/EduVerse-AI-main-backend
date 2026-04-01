from bson import ObjectId
from datetime import datetime
from typing import List

from app.db.database import db
from app.crud.courses.helpers import get_collections, serialize_course, validate_id

courses_col, students_col, _ = get_collections()


async def _is_completed_course_attempt(student: dict, course_id: str) -> bool:
    """Return True when the student has already finished this course."""
    user_id = student.get("userId")
    if not user_id:
        return False

    progress = await db.student_progress.find_one(
        {
            "studentId": str(user_id),
            "courseId": {"$in": [course_id, ObjectId(course_id)]},
        }
    )
    if not progress:
        return False

    return bool(
        progress.get("isCompleted") or (progress.get("progressPercentage") or 0) >= 100
    )


async def _reset_completed_course_attempt(student: dict, course_id: str) -> None:
    """
    Clear course-specific learning artifacts so a completed course can be taken again.

    We intentionally keep the enrollment itself, but reset progress and generated data
    tied to the previous attempt.
    """
    student_oid = student["_id"]
    student_id = str(student_oid)
    user_id = str(student.get("userId")) if student.get("userId") else None
    course_oid = ObjectId(course_id)

    course_match = {"$in": [course_oid, course_id]}
    student_match = {"$in": [student_oid, student_id]}

    await db.student_progress.delete_many(
        {
            "studentId": {"$in": [user_id, student.get("userId")]},
            "courseId": course_match,
        }
    )
    await db.aiGeneratedLessons.delete_many(
        {"studentId": student_match, "courseId": course_match}
    )
    await db.studentClassifications.delete_many(
        {"studentId": student_match, "courseId": course_match}
    )
    await db.aiQuizSessions.delete_many(
        {
            "studentId": {"$in": [student_id, student_oid]},
            "courseId": course_match,
        }
    )
    await db.quizSubmissions.delete_many(
        {"studentId": student_match, "courseId": course_match}
    )
    await db.aiChatHistory.delete_many(
        {
            "studentId": {"$in": [student_id, student_oid]},
            "courseId": course_match,
        }
    )
    await students_col.update_one(
        {"_id": student_oid},
        {
            "$pull": {"completedCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )


async def _verify_tenant_entity(collection, entity_id: str, tenant_id: str, label: str):
    """Verifies entity exists within tenant. Returns (entity, error_dict)."""
    oid, toid = ObjectId(entity_id), ObjectId(tenant_id)
    doc = await collection.find_one({"_id": oid, "tenantId": toid})
    if doc:
        return doc, None
    if await collection.find_one({"_id": oid}):
        return None, {
            "success": False,
            "message": f"{label} belongs to different tenant",
        }
    return None, {"success": False, "message": f"{label} not found: {entity_id}"}


async def _get_course_for_enrollment(
    course_id: str, tenant_id: str | None, enforce_same_tenant: bool
):
    if enforce_same_tenant:
        if not tenant_id:
            return None, {"success": False, "message": "Tenant context required"}
        return await _verify_tenant_entity(courses_col, course_id, tenant_id, "Course")

    oid = ObjectId(course_id)
    course = await courses_col.find_one({"_id": oid})
    if not course:
        return None, {"success": False, "message": f"Course not found: {course_id}"}
    return course, None


async def _get_student_for_enrollment(
    student_id: str, tenant_id: str | None, enforce_same_tenant: bool
):
    if enforce_same_tenant:
        if not tenant_id:
            return None, {"success": False, "message": "Tenant context required"}
        return await _verify_tenant_entity(
            students_col, student_id, tenant_id, "Student"
        )

    student = await students_col.find_one({"_id": ObjectId(student_id)})
    if not student:
        return None, {"success": False, "message": f"Student not found: {student_id}"}
    return student, None


async def enroll_student(
    course_id: str,
    student_id: str,
    tenant_id: str | None,
    *,
    enforce_same_tenant: bool = False,
) -> dict:
    for id_val, label in [
        (course_id, "course"),
        (student_id, "student"),
    ]:
        err = validate_id(id_val, label)
        if err:
            return err
    if tenant_id:
        err = validate_id(tenant_id, "tenant")
        if err:
            return err
    if enforce_same_tenant and not tenant_id:
        return {"success": False, "message": "Tenant context required"}

    course, err = await _get_course_for_enrollment(
        course_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err
    student, err = await _get_student_for_enrollment(
        student_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err

    if course_id in student.get("enrolledCourses", []):
        if await _is_completed_course_attempt(student, course_id):
            await _reset_completed_course_attempt(student, course_id)
            return {
                "success": True,
                "message": "Re-enrolled successfully",
                "reenrolled": True,
            }
        return {"success": False, "message": "Already enrolled"}

    student_filter = {"_id": ObjectId(student_id)}
    if enforce_same_tenant and tenant_id:
        student_filter["tenantId"] = ObjectId(tenant_id)

    await students_col.update_one(
        student_filter,
        {
            "$addToSet": {"enrolledCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )
    await courses_col.update_one(
        {"_id": course["_id"]},
        {"$inc": {"enrolledStudents": 1}, "$set": {"updatedAt": datetime.utcnow()}},
    )

    # Lazy-init tenant gamification record
    course_tenant = str(course.get("tenantId"))
    if course_tenant:
        perf_exists = await db.student_performance.find_one({
            "studentId": ObjectId(student_id),
            "tenantId": ObjectId(course_tenant)
        })
        if not perf_exists:
            user = await db.users.find_one({"_id": student.get("userId")})
            await db.student_performance.insert_one({
                "studentId": ObjectId(student_id),
                "studentName": user.get("fullName", "Student") if user else "Student",
                "tenantId": ObjectId(course_tenant),
                "userId": student.get("userId"),
                "totalPoints": 0, "pointsThisWeek": 0, "xp": 0, "level": 1,
                "xpToNextLevel": 300, "badges": [], "certificates": [],
                "weeklyStudyTime": [], "courseStats": [],
                "createdAt": datetime.utcnow()
            })

    return {"success": True, "message": "Enrolled successfully"}


async def unenroll_student(
    course_id: str,
    student_id: str,
    tenant_id: str | None,
    *,
    enforce_same_tenant: bool = False,
) -> dict:
    for id_val, label in [
        (course_id, "course"),
        (student_id, "student"),
    ]:
        err = validate_id(id_val, label)
        if err:
            return err
    if tenant_id:
        err = validate_id(tenant_id, "tenant")
        if err:
            return err
    if enforce_same_tenant and not tenant_id:
        return {"success": False, "message": "Tenant context required"}

    course, err = await _get_course_for_enrollment(
        course_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err
    student, err = await _get_student_for_enrollment(
        student_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err

    if course_id not in student.get("enrolledCourses", []):
        return {"success": False, "message": "Not enrolled in this course"}

    student_filter = {"_id": ObjectId(student_id)}
    if enforce_same_tenant and tenant_id:
        student_filter["tenantId"] = ObjectId(tenant_id)

    await students_col.update_one(
        student_filter,
        {
            "$pull": {"enrolledCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )
    await courses_col.update_one(
        {"_id": course["_id"]},
        {"$inc": {"enrolledStudents": -1}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return {"success": True, "message": "Unenrolled successfully"}


async def reorder_lessons(
    course_id: str, tenant_id: str, module_id: str, lesson_ids: List[str]
) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    modules = course.get("modules", [])
    found = False
    for mod in modules:
        if mod.get("id") == module_id:
            found = True
            lesson_map = {l.get("id"): l for l in mod.get("lessons", [])}
            reordered = [
                dict(lesson_map[lid], order=i)
                for i, lid in enumerate(lesson_ids)
                if lid in lesson_map
            ]
            for l in mod.get("lessons", []):
                if l.get("id") not in lesson_ids:
                    l["order"] = len(reordered)
                    reordered.append(l)
            mod["lessons"] = reordered
            break

    if not found:
        return {"success": False, "message": f"Module not found: {module_id}"}

    res = await courses_col.update_one(
        {"_id": ObjectId(course_id)},
        {"$set": {"modules": modules, "updatedAt": datetime.now()}},
    )
    if res.modified_count == 0:
        return {"success": False, "message": "Failed to reorder"}

    updated = await courses_col.find_one({"_id": ObjectId(course_id)})
    return {
        "success": True,
        "message": "Lessons reordered",
        "course": serialize_course(updated),
    }


async def reorder_modules(
    course_id: str, tenant_id: str, module_ids: List[str]
) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    mod_map = {m.get("id"): m for m in course.get("modules", [])}
    reordered = [
        dict(mod_map[mid], order=i)
        for i, mid in enumerate(module_ids)
        if mid in mod_map
    ]
    for m in course.get("modules", []):
        if m.get("id") not in module_ids:
            m["order"] = len(reordered)
            reordered.append(m)

    res = await courses_col.update_one(
        {"_id": ObjectId(course_id)},
        {"$set": {"modules": reordered, "updatedAt": datetime.now()}},
    )
    if res.modified_count == 0:
        return {"success": False, "message": "Failed to reorder"}

    updated = await courses_col.find_one({"_id": ObjectId(course_id)})
    return {
        "success": True,
        "message": "Modules reordered",
        "course": serialize_course(updated),
    }
