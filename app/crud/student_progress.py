from bson import ObjectId
from datetime import datetime
from typing import List, Optional
from app.db.database import db
from app.schemas.student_progress import CourseProgress

class ProgressCRUD:
    def __init__(self):
        self.collection = db.student_progress

    async def get_or_create_progress(self, student_id: str, course_id: str, tenant_id: str = None) -> dict:
        """Fetch course progress or initialize if not exists."""
        actual_tenant_oid = None
        if tenant_id and ObjectId.is_valid(tenant_id):
            actual_tenant_oid = ObjectId(tenant_id)
        else:
            course = await db.courses.find_one({"_id": ObjectId(course_id)})
            if course and course.get("tenantId"):
                actual_tenant_oid = course.get("tenantId")

        query = {
            "studentId": student_id,
            "courseId": course_id,
        }
        if actual_tenant_oid:
            query["tenantId"] = actual_tenant_oid

        progress = await self.collection.find_one(query)

        if not progress:
            progress = {
                "studentId": student_id,
                "courseId": course_id,
                "tenantId": actual_tenant_oid,
                "completedLessons": [],
                "progressPercentage": 0,
                "isCompleted": False,
                "lastAccessedAt": datetime.utcnow(),
                "enrollmentDate": datetime.utcnow()
            }
            result = await self.collection.insert_one(progress)
            progress["_id"] = str(result.inserted_id)
        else:
            progress["_id"] = str(progress["_id"])

        progress["tenantId"] = (
            str(progress.get("tenantId")) if progress.get("tenantId") else None
        )
        return progress

    async def mark_lesson_complete(
        self,
        student_id: str,
        course_id: str,
        tenant_id: str = None,
        lesson_id: str = None
    ) -> dict:
        """Mark a lesson as complete and update course percentage."""

        # ── 1. Load course ────────────────────────────────────────────────────
        course = await db.courses.find_one({"_id": ObjectId(course_id)})
        if not course:
            raise ValueError("Course not found")

        actual_tenant_oid = course.get("tenantId")
        actual_tenant_id = str(actual_tenant_oid) if actual_tenant_oid else tenant_id

        # ── 2. Single pass: count lessons, find current title & next topic ────
        # BUG FIX: previously two separate loops — the second loop reset
        # lesson_title back to "Unknown Lesson" before it could be used.
        total_lessons = 0
        lesson_title = "Unknown Lesson"
        found_current = False

        for module in course.get("modules", []):
            for lesson in module.get("lessons", []):
                total_lessons += 1
                # Normalise both sides to strings to avoid ObjectId/string mismatch
                if str(lesson.get("id")) == str(lesson_id):
                    lesson_title = lesson.get("title", "Unknown Lesson")
                    found_current = True

        if total_lessons == 0:
            total_lessons = 1  # prevent division by zero

        # ── 3. Build query ────────────────────────────────────────────────────
        query = {"studentId": student_id, "courseId": course_id}
        if actual_tenant_oid:
            query["tenantId"] = actual_tenant_oid

        # ── 4. Check if already completed (deduplication guard) ───────────────
        # BUG FIX: without this, every frontend retry fires another background
        # AI task and hammers the Gemini quota.
        existing = await self.collection.find_one(query)
        already_completed_this_lesson = (
            existing is not None
            and lesson_id in existing.get("completedLessons", [])
        )
        was_completed_before = (
            existing is not None and bool(existing.get("isCompleted"))
        )

        # ── 5. Update completed lessons ───────────────────────────────────────
        await self.collection.update_one(
            query,
            {
                "$addToSet": {"completedLessons": lesson_id},
                "$set": {"lastAccessedAt": datetime.utcnow()}
            },
            upsert=True
        )

        # ── 6. Recalculate percentage ─────────────────────────────────────────
        progress_doc = await self.collection.find_one(query)
        completed_count = len(progress_doc.get("completedLessons", []))
        percentage = int((completed_count / total_lessons) * 100)
        is_completed = percentage >= 100

        await self.collection.update_one(
            query,
            {"$set": {"progressPercentage": percentage, "isCompleted": is_completed}}
        )

        # ── 7. Reward system + adaptive AI pipeline ───────────────────────────
        from app.crud.student_performance import StudentPerformanceCRUD
        import asyncio
        from app.services.quiz_generator import generate_ai_quiz

        student_doc = await db.students.find_one({"userId": ObjectId(student_id)})
        if student_doc:
            internal_student_id = str(student_doc["_id"])

            # Only fire AI generation if this lesson wasn't already completed.
            # This prevents duplicate quiz tasks on frontend retries.
            if not already_completed_this_lesson:
                async def background_adaptive_pipeline():
                    import logging as _logging
                    _log = _logging.getLogger(__name__)
                    try:
                        tenant_str = str(actual_tenant_oid) if actual_tenant_oid else tenant_id
                        _log.info(
                            "Adaptive Pipeline: generating quiz for '%s'",
                            lesson_title
                        )
                        # Generate quiz for the lesson just completed
                        await generate_ai_quiz(
                            student_id=internal_student_id,
                            course_id=course_id,
                            topic=lesson_title,
                            tenant_id=tenant_str,
                            lesson_id=lesson_id,
                        )
                        _log.info("Adaptive Pipeline: quiz generated for '%s'", lesson_title)
                        _log.info(
                            "Adaptive Pipeline: next adaptive lesson will be generated after quiz submission"
                        )
                    except Exception as exc:
                        _log.error("Adaptive Pipeline error: %s", exc)

                asyncio.create_task(background_adaptive_pipeline())
            else:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "Lesson '%s' already completed — skipping duplicate AI generation.",
                    lesson_id
                )

            # ── Performance tracking (every lesson, not just 100%) ────────────
            perf = await StudentPerformanceCRUD.get_student_performance(
                internal_student_id, actual_tenant_id
            )
            if not perf:
                await StudentPerformanceCRUD.create_performance_record(
                    student_id=internal_student_id,
                    student_name=student_doc.get("fullName", "Student"),
                    tenant_id=actual_tenant_id,
                    user_id=student_id
                )
                perf = await StudentPerformanceCRUD.get_student_performance(
                    internal_student_id, actual_tenant_id
                )

            if perf:
                await StudentPerformanceCRUD.update_course_progress(
                    internal_student_id, actual_tenant_id, course_id,
                    percentage, datetime.utcnow().isoformat()
                )

                just_completed = is_completed and not was_completed_before

                if just_completed:
                    await StudentPerformanceCRUD.add_points(
                        internal_student_id,
                        actual_tenant_id,
                        100,
                        reason=f"Course completion: {course.get('title', 'Course')}",
                        course_id=course_id,
                    )

                    if course.get("hasBadges"):
                        refreshed_perf = await StudentPerformanceCRUD.get_student_performance(
                            internal_student_id, actual_tenant_id
                        )
                        already_has_badge = any(
                            b.get("courseId") == course_id
                            and b.get("name") == "Course Expert"
                            for b in (refreshed_perf or {}).get("badges", [])
                        )
                        if not already_has_badge:
                            await StudentPerformanceCRUD.add_badge(
                                internal_student_id, actual_tenant_id, {
                                    "courseId": course_id,
                                    "name": "Course Expert",
                                    "icon": "course_gold.png"
                                }
                            )

        return {
            "courseId": course_id,
            "progressPercentage": percentage,
            "completedLessons": progress_doc.get("completedLessons", []),
            "isCompleted": is_completed,
            "lastAccessedAt": datetime.utcnow()
        }

    async def get_student_course_progress(
        self, student_id: str, tenant_id: str | None = None
    ) -> List[dict]:
        """Get progress for all courses a student is enrolled in."""
        query = {"studentId": student_id}
        if tenant_id and ObjectId.is_valid(tenant_id):
            query["tenantId"] = ObjectId(tenant_id)

        cursor = self.collection.find(query)
        results = await cursor.to_list(length=100)
        for r in results:
            r["_id"] = str(r["_id"])
            if "tenantId" in r:
                r["tenantId"] = str(r["tenantId"])
        return results


progress_crud = ProgressCRUD()
