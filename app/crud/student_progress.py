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
        # Clean tenant_id or derive from course
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
            
        progress["tenantId"] = str(progress.get("tenantId"))
        return progress

    async def mark_lesson_complete(self, student_id: str, course_id: str, tenant_id: str = None, lesson_id: str = None) -> dict:
        """Mark a lesson as complete and update course percentage."""
        # 1. Get total lessons from course
        course = await db.courses.find_one({"_id": ObjectId(course_id)})
        if not course:
            raise ValueError("Course not found")

        actual_tenant_oid = course.get("tenantId")

        query = {
            "studentId": student_id,
            "courseId": course_id
        }
        if actual_tenant_oid:
            query["tenantId"] = actual_tenant_oid
            
        total_lessons = 0
        for m in course.get("modules", []):
            total_lessons += len(m.get("lessons", []))
            
        if total_lessons == 0:
            total_lessons = 1 # Prevent division by zero
            
        # 2. Update completed lessons list
        await self.collection.update_one(
            query,
            {
                "$addToSet": {"completedLessons": lesson_id},
                "$set": {"lastAccessedAt": datetime.utcnow()}
            },
            upsert=True
        )
        
        # 3. Recalculate percentage
        progress_doc = await self.collection.find_one(query)
        completed_count = len(progress_doc.get("completedLessons", []))
        percentage = int((completed_count / total_lessons) * 100)
        
        is_completed = (percentage >= 100)
        
        # 4. Save final state
        await self.collection.update_one(
            query,
            {
                "$set": {
                    "progressPercentage": percentage,
                    "isCompleted": is_completed
                }
            }
        )
        
        # --- NEW: Reward System Integration ---
        if is_completed:
            from app.crud.student_performance import StudentPerformanceCRUD
            
            # Find the internal student record to get studentId for performance
            student_doc = await db.students.find_one({"userId": ObjectId(student_id)})
            if student_doc:
                internal_student_id = str(student_doc["_id"])
                
                # 1. Update performance record course stats
                await StudentPerformanceCRUD.update_course_progress(
                    internal_student_id, tenant_id, course_id, percentage, datetime.utcnow().isoformat()
                )
                
                # 2. Award Points for completion
                await StudentPerformanceCRUD.add_points(internal_student_id, tenant_id, 100) # Bonus for completion
                
                # 3. Award Certificate if course qualifies
                if course.get("hasCertificate"):
                    # Check if already awarded
                    perf = await StudentPerformanceCRUD.get_student_performance(internal_student_id, tenant_id)
                    already_has_cert = any(c.get("courseId") == course_id for c in perf.get("certificates", []))
                    
                    if not already_has_cert:
                        await StudentPerformanceCRUD.add_certificate(internal_student_id, tenant_id, {
                            "courseId": course_id,
                            "courseName": course.get("title"),
                            "issuedBy": "EduVerse AI",
                            "type": "Course Completion"
                        })
                
                # 4. Award Badge if course qualifies (Already partially handled in update_course_progress, but let's be explicit)
                if course.get("hasBadges"):
                    perf = await StudentPerformanceCRUD.get_student_performance(internal_student_id, tenant_id)
                    already_has_badge = any(b.get("courseId") == course_id and b.get("name") == "Course Expert" for b in perf.get("badges", []))
                    
                    if not already_has_badge:
                        await StudentPerformanceCRUD.add_badge(internal_student_id, tenant_id, {
                            "courseId": course_id,
                            "name": "Course Expert",
                            "icon": "course_gold.png"
                        })

        return {
            "courseId": course_id,
            "progressPercentage": percentage,
            "completedLessons": progress_doc.get("completedLessons", []),
            "isCompleted": is_completed,
            "lastAccessedAt": datetime.utcnow()
        }

    async def get_student_course_progress(self, student_id: str, tenant_id: str) -> List[dict]:
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
