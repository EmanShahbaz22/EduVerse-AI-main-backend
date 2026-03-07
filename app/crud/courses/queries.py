import re
from bson import ObjectId
from typing import Optional, Dict
from app.db.database import db
from app.crud.courses.helpers import (
    get_collections,
    validate_id,
    get_enriched_courses_pipeline,
    serialize_course,
)

courses_col, students_col, users_col = get_collections()


async def get_course_by_id(course_id: str, tenant_id: str) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return {**err, "course": None}

    query = {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    pipeline = get_enriched_courses_pipeline(query, 0, 1)
    results = await courses_col.aggregate(pipeline).to_list(length=1)
    if not results:
        return {"success": False, "message": "Course not found", "course": None}
    return {
        "success": True,
        "message": "Course found",
        "course": serialize_course(results[0]),
    }


async def get_course_by_id_any_tenant(course_id: str, *, public_only: bool = True) -> dict:
    err = validate_id(course_id, "course")
    if err:
        return {**err, "course": None}

    query: Dict = {"_id": ObjectId(course_id)}
    if public_only:
        query["isPublic"] = True

    pipeline = get_enriched_courses_pipeline(query, 0, 1)
    results = await courses_col.aggregate(pipeline).to_list(length=1)
    if not results:
        return {"success": False, "message": "Course not found", "course": None}
    return {
        "success": True,
        "message": "Course found",
        "course": serialize_course(results[0]),
    }


async def get_all_courses(
    tenant_id: str,
    teacher_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> dict:
    err = validate_id(tenant_id, "tenant")
    if err:
        return {**err, "courses": [], "total": 0}

    query: Dict = {"tenantId": ObjectId(tenant_id)}

    if teacher_id:
        if not ObjectId.is_valid(teacher_id):
            return {
                "success": False,
                "message": f"Invalid teacher ID: {teacher_id}",
                "courses": [],
                "total": 0,
            }
        query["teacherId"] = ObjectId(teacher_id)

    if status:
        query["status"] = {"$regex": f"^{status.strip()}$", "$options": "i"}
    if category:
        query["category"] = {"$regex": f"^{category.strip()}$", "$options": "i"}
    if search:
        escaped = re.escape(search.strip())
        query["$or"] = [
            {"title": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
            {"category": {"$regex": escaped, "$options": "i"}},
            {"courseCode": {"$regex": escaped, "$options": "i"}},
        ]

    try:
        total = await courses_col.count_documents(query)
        pipeline = get_enriched_courses_pipeline(query, skip, limit)
        courses = await courses_col.aggregate(pipeline).to_list(length=limit)
        courses = [serialize_course(c) for c in courses]
        return {
            "success": True,
            "message": f"Found {len(courses)} courses",
            "courses": courses,
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    except Exception as e:
        return {"success": False, "message": f"Error: {e}", "courses": [], "total": 0}


async def get_marketplace_courses(
    teacher_id: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> dict:
    query: Dict = {
        "isPublic": True,
        "status": {"$regex": "^published$", "$options": "i"},
    }

    if teacher_id:
        if not ObjectId.is_valid(teacher_id):
            return {
                "success": False,
                "message": f"Invalid teacher ID: {teacher_id}",
                "courses": [],
                "total": 0,
            }
        query["teacherId"] = ObjectId(teacher_id)

    if category:
        query["category"] = {"$regex": f"^{category.strip()}$", "$options": "i"}
    if search:
        escaped = re.escape(search.strip())
        query["$or"] = [
            {"title": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
            {"category": {"$regex": escaped, "$options": "i"}},
            {"courseCode": {"$regex": escaped, "$options": "i"}},
        ]

    try:
        total = await courses_col.count_documents(query)
        pipeline = get_enriched_courses_pipeline(query, skip, limit)
        courses = await courses_col.aggregate(pipeline).to_list(length=limit)
        courses = [serialize_course(c) for c in courses]
        return {
            "success": True,
            "message": f"Found {len(courses)} marketplace courses",
            "courses": courses,
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    except Exception as e:
        return {"success": False, "message": f"Error: {e}", "courses": [], "total": 0}


async def get_student_courses(student_id: str, tenant_id: str) -> dict:
    err = validate_id(student_id, "student") or validate_id(tenant_id, "tenant")
    if err:
        return {**err, "courses": []}

    student = await students_col.find_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}
    )
    if not student:
        exists = await students_col.find_one({"_id": ObjectId(student_id)})
        msg = (
            "Student belongs to different tenant"
            if exists
            else f"Student not found: {student_id}"
        )
        return {"success": False, "message": msg, "courses": []}

    enrolled = student.get("enrolledCourses", [])
    if not enrolled:
        return {"success": True, "message": "No enrolled courses", "courses": []}

    course_ids = [ObjectId(cid) for cid in enrolled if ObjectId.is_valid(cid)]
    if not course_ids:
        return {"success": True, "message": "Invalid course enrollments", "courses": []}

    pipeline = get_enriched_courses_pipeline({"_id": {"$in": course_ids}}, 0, 100)
    courses = await courses_col.aggregate(pipeline).to_list(length=100)

    # Merge progress data
    progress_user_id = str(student.get("userId") or student_id)
    progress_cursor = db.student_progress.find(
        {
            "studentId": progress_user_id,
            "courseId": {"$in": [str(c["_id"]) for c in courses]},
            "tenantId": ObjectId(tenant_id),
        }
    )
    progress_map = {p["courseId"]: p for p in await progress_cursor.to_list(length=100)}

    enriched = []
    for course in courses:
        cid = str(course["_id"])
        prog = progress_map.get(cid, {})
        total_lessons = sum(
            len((m.get("lessons") or [])) for m in (course.get("modules") or [])
        )
        all_lessons = [
            l for m in (course.get("modules") or []) for l in (m.get("lessons") or [])
        ]
        completed = set(str(lid) for lid in prog.get("completedLessons", []))

        next_lesson = "Upcoming Content"
        if total_lessons > 0:
            if len(completed) >= total_lessons:
                next_lesson = "Course Finished! 🎉"
            else:
                for lesson in all_lessons:
                    lid = str(lesson.get("id") or lesson.get("_id") or "")
                    if lid and lid not in completed:
                        next_lesson = lesson.get("title", "Next Lesson")
                        break

        course["progress"] = prog.get("progressPercentage", 0)
        course["lessonsCompleted"] = len(completed)
        course["totalLessons"] = total_lessons
        course["nextLesson"] = next_lesson
        enriched.append(serialize_course(course))

    return {
        "success": True,
        "message": f"Found {len(enriched)} enrolled courses",
        "courses": enriched,
    }


async def get_student_courses_any_tenant(student_id: str) -> dict:
    err = validate_id(student_id, "student")
    if err:
        return {**err, "courses": []}

    student = await students_col.find_one({"_id": ObjectId(student_id)})
    if not student:
        return {"success": False, "message": f"Student not found: {student_id}", "courses": []}

    enrolled = student.get("enrolledCourses", [])
    if not enrolled:
        return {"success": True, "message": "No enrolled courses", "courses": []}

    course_ids = [ObjectId(cid) for cid in enrolled if ObjectId.is_valid(cid)]
    if not course_ids:
        return {"success": True, "message": "Invalid course enrollments", "courses": []}

    pipeline = get_enriched_courses_pipeline({"_id": {"$in": course_ids}}, 0, 100)
    courses = await courses_col.aggregate(pipeline).to_list(length=100)

    progress_user_id = str(student.get("userId") or student_id)
    progress_cursor = db.student_progress.find(
        {
            "studentId": progress_user_id,
            "courseId": {"$in": [str(c["_id"]) for c in courses]},
        }
    )
    progress_map = {p["courseId"]: p for p in await progress_cursor.to_list(length=100)}

    enriched = []
    for course in courses:
        cid = str(course["_id"])
        prog = progress_map.get(cid, {})
        total_lessons = sum(
            len((m.get("lessons") or [])) for m in (course.get("modules") or [])
        )
        all_lessons = [
            l for m in (course.get("modules") or []) for l in (m.get("lessons") or [])
        ]
        completed = set(str(lid) for lid in prog.get("completedLessons", []))

        next_lesson = "Upcoming Content"
        if total_lessons > 0:
            if len(completed) >= total_lessons:
                next_lesson = "Course Finished! 🎉"
            else:
                for lesson in all_lessons:
                    lid = str(lesson.get("id") or lesson.get("_id") or "")
                    if lid and lid not in completed:
                        next_lesson = lesson.get("title", "Next Lesson")
                        break

        course["progress"] = prog.get("progressPercentage", 0)
        course["lessonsCompleted"] = len(completed)
        course["totalLessons"] = total_lessons
        course["nextLesson"] = next_lesson
        enriched.append(serialize_course(course))

    return {
        "success": True,
        "message": f"Found {len(enriched)} enrolled courses",
        "courses": enriched,
    }


async def get_enrolled_students(course_id: str, tenant_id: str) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    tenant_oid = ObjectId(tenant_id)
    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": tenant_oid}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    pipeline = [
        {"$match": {"tenantId": tenant_oid, "enrolledCourses": course_id}},
        {
            "$lookup": {
                "from": "users",
                "localField": "userId",
                "foreignField": "_id",
                "as": "user",
            }
        },
        {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
    ]

    students = []
    async for s in students_col.aggregate(pipeline):
        created_at = s.get("createdAt")
        students.append(
            {
                "_id": str(s["_id"]),
                "id": str(s["_id"]),
                "fullName": s.get("user", {}).get(
                    "fullName", s.get("fullName", "Unknown")
                ),
                "email": s.get("user", {}).get("email", s.get("email", "")),
                "enrolledAt": created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at or ""),
                "progress": s.get("progress", {}).get(course_id, 0)
                if isinstance(s.get("progress"), dict)
                else 0,
                "lessonsCompleted": s.get("lessonsCompleted", {}).get(course_id, 0)
                if isinstance(s.get("lessonsCompleted"), dict)
                else 0,
                "lastAccessed": s.get("lastAccessed", {}).get(course_id)
                if isinstance(s.get("lastAccessed"), dict)
                else None,
            }
        )
    return {"success": True, "students": students, "count": len(students)}
