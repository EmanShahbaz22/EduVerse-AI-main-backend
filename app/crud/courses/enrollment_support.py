from datetime import datetime

from bson import ObjectId

from app.db.database import db


async def is_completed_course_attempt(student: dict, course_id: str) -> bool:
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


async def reset_completed_course_attempt(
    students_collection,
    student: dict,
    course_id: str,
) -> None:
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
    await students_collection.update_one(
        {"_id": student_oid},
        {
            "$pull": {"completedCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )


async def ensure_student_tenant_performance(course: dict, student: dict) -> None:
    """Create the tenant-scoped performance record lazily for enrolled students."""
    course_tenant = str(course.get("tenantId"))
    if not course_tenant:
        return

    perf_exists = await db.student_performance.find_one(
        {
            "studentId": ObjectId(str(student["_id"])),
            "tenantId": ObjectId(course_tenant),
        }
    )
    if perf_exists:
        return

    user = await db.users.find_one({"_id": student.get("userId")})
    await db.student_performance.insert_one(
        {
            "studentId": ObjectId(str(student["_id"])),
            "studentName": user.get("fullName", "Student") if user else "Student",
            "tenantId": ObjectId(course_tenant),
            "userId": student.get("userId"),
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
        }
    )
