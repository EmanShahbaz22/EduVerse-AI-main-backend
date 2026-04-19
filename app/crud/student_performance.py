from bson import ObjectId
from datetime import datetime
from app.db.database import student_performance_collection
from app.db.database import db
from app.utils.mongo import fix_object_ids
import os
from app.crud.student_performance_support import (
    build_filtered_certificates,
    build_global_course_stats,
    build_global_points_history,
    build_tenant_course_stats,
    certificate_download_name,
    collapse_leaderboard_docs,
    coerce_datetime,
    compute_points_this_week,
    generate_certificate_file,
    get_certificate_path,
    history_key,
    update_level_system,
)

COL = student_performance_collection
CERTIFICATE_TEMPLATE_VERSION = "v2"


def safe_oid(val):
    if not val: return None
    try:
        return ObjectId(val) if isinstance(val, str) else val
    except Exception:
        return None

def _query(student_id: str, tenant_id: str):
    s_oid = safe_oid(student_id)
    t_oid = safe_oid(tenant_id)
    return {"studentId": s_oid, "tenantId": t_oid}


class StudentPerformanceCRUD:
    @staticmethod
    def _coerce_datetime(value):
        return coerce_datetime(value)

    @staticmethod
    def _history_key(item: dict):
        return history_key(item)

    @staticmethod
    def _compute_points_this_week(points_history: list[dict]) -> int:
        return compute_points_this_week(points_history)

    @staticmethod
    def _get_certificate_path(file_id: str) -> str:
        return get_certificate_path(file_id)

    @staticmethod
    def _certificate_download_name(course_name: str) -> str:
        return certificate_download_name(course_name)

    @staticmethod
    async def _ensure_performance_record(student_id: str, tenant_id: str):
        doc = await COL.find_one(_query(student_id, tenant_id))
        if doc:
            return doc

        student_query = {"_id": safe_oid(student_id)}
        tenant_oid = safe_oid(tenant_id)
        if tenant_oid:
            student_query["tenantId"] = tenant_oid

        student = await db.students.find_one(student_query)
        if not student:
            student = await db.students.find_one({"_id": safe_oid(student_id)})
        if not student:
            return None

        user = await db.users.find_one({"_id": student.get("userId")})
        await StudentPerformanceCRUD.create_performance_record(
            student_id=student_id,
            student_name=(user or {}).get("fullName", "Student"),
            tenant_id=tenant_id,
            user_id=str(student.get("userId")) if student.get("userId") else None,
        )
        return await COL.find_one(_query(student_id, tenant_id))

    @staticmethod
    async def _generate_certificate_file(student_name: str, course_name: str) -> str:
        return await generate_certificate_file(student_name, course_name)

    @staticmethod
    async def _ensure_course_certificate(
        student_id: str,
        tenant_id: str,
        course_id: str,
        course_name: str,
        student_name: str,
    ):
        q = _query(student_id, tenant_id)
        perf = await COL.find_one(q, {"certificates": 1})
        certificates = (perf or {}).get("certificates", [])
        expected_title = course_name
        valid_existing = next(
            (
                cert
                for cert in certificates
                if cert.get("courseId") == course_id
                and cert.get("file")
                and cert.get("title")
                and cert.get("title") == expected_title
                and cert.get("generatedWith") == CERTIFICATE_TEMPLATE_VERSION
                and os.path.isfile(StudentPerformanceCRUD._get_certificate_path(cert.get("file")))
            ),
            None,
        )
        if valid_existing:
            return

        await COL.update_one(q, {"$pull": {"certificates": {"courseId": course_id}}})
        file_id = await StudentPerformanceCRUD._generate_certificate_file(
            student_name, course_name
        )
        await COL.update_one(
            q,
            {
                "$push": {
                    "certificates": {
                        "courseId": course_id,
                        "title": expected_title,
                        "file": file_id,
                        "date": datetime.utcnow(),
                        "generatedWith": CERTIFICATE_TEMPLATE_VERSION,
                    }
                }
            },
        )

    @staticmethod
    async def sync_from_progress(student_id: str, tenant_id: str):
        perf = await StudentPerformanceCRUD._ensure_performance_record(
            student_id, tenant_id
        )
        if not perf:
            return None

        student = await db.students.find_one({"_id": safe_oid(student_id)})
        if not student:
            return fix_object_ids(perf)

        progress_query = {"studentId": str(student.get("userId") or student_id)}
        tenant_oid = safe_oid(tenant_id)
        if tenant_oid:
            progress_query["tenantId"] = tenant_oid

        progress_docs = await db.student_progress.find(progress_query).to_list(length=None)
        raw_perf = await COL.find_one(_query(student_id, tenant_id)) or perf

        course_stats, completed_course_ids = build_tenant_course_stats(progress_docs)
        course_ids = [
            safe_oid(progress.get("courseId"))
            for progress in progress_docs
            if safe_oid(progress.get("courseId"))
        ]
        courses = await db.courses.find({"_id": {"$in": course_ids}}).to_list(length=None) if course_ids else []
        course_map = {str(course["_id"]): course for course in courses}

        updates = {"$set": {"courseStats": course_stats}}
        existing_history = raw_perf.get("pointsHistory", [])
        rewarded_course_ids = {
            str(item.get("courseId"))
            for item in existing_history
            if item.get("courseId")
        }
        new_points_entries = []

        for course_id in completed_course_ids:
            course = course_map.get(course_id)
            course_name = (course or {}).get("title", "Course")

            if course_id not in rewarded_course_ids:
                new_points_entries.append(
                    {
                        "points": 100,
                        "reason": f"Course completion: {course_name}",
                        "courseId": course_id,
                        "date": datetime.utcnow(),
                    }
                )

            if (course or {}).get("hasCertificate"):
                await StudentPerformanceCRUD._ensure_course_certificate(
                    student_id=student_id,
                    tenant_id=tenant_id,
                    course_id=course_id,
                    course_name=course_name,
                    student_name=raw_perf.get("studentName", "Student"),
                )

        if new_points_entries:
            total_points = raw_perf.get("totalPoints", 0) + sum(
                item["points"] for item in new_points_entries
            )
            xp = raw_perf.get("xp", 0) + sum(item["points"] for item in new_points_entries)
            level_data = StudentPerformanceCRUD._update_level_system(
                {"xp": xp, "level": raw_perf.get("level", 1)}
            )
            updates["$set"].update(
                {
                    "totalPoints": total_points,
                    "xp": level_data["xp"],
                    "level": level_data["level"],
                    "xpToNextLevel": level_data["xpToNextLevel"],
                }
            )
            updates["$push"] = {"pointsHistory": {"$each": new_points_entries}}

        await COL.update_one(_query(student_id, tenant_id), updates)
        synced = await COL.find_one(_query(student_id, tenant_id))
        return fix_object_ids(synced) if synced else None

    @staticmethod
    async def sync_global_from_progress(student_id: str):
        perf = await StudentPerformanceCRUD._ensure_performance_record(student_id, None)
        if not perf:
            return None

        student = await db.students.find_one({"_id": safe_oid(student_id)})
        if not student:
            return fix_object_ids(perf)

        progress_docs = await db.student_progress.find(
            {"studentId": str(student.get("userId") or student_id)}
        ).to_list(length=None)

        all_perf_docs = await COL.find({"studentId": safe_oid(student_id)}).to_list(length=None)
        raw_perf = next((doc for doc in all_perf_docs if not doc.get("tenantId")), None) or perf

        course_ids = {
            str(progress.get("courseId"))
            for progress in progress_docs
            if progress.get("courseId")
        }
        courses = (
            await db.courses.find({"_id": {"$in": [safe_oid(course_id) for course_id in course_ids]}}).to_list(length=None)
            if course_ids
            else []
        )
        course_map = {str(course["_id"]): course for course in courses}

        course_stats_map, completed_course_ids = build_global_course_stats(progress_docs)

        existing_history = []
        for doc in all_perf_docs:
            existing_history.extend(doc.get("pointsHistory", []) or [])

        for course_id in completed_course_ids:
            course = course_map.get(course_id)
            course_name = (course or {}).get("title", "Course")

            if (course or {}).get("hasCertificate"):
                await StudentPerformanceCRUD._ensure_course_certificate(
                    student_id=student_id,
                    tenant_id=None,
                    course_id=course_id,
                    course_name=course_name,
                    student_name=raw_perf.get("studentName", "Student"),
                )

        points_history = build_global_points_history(
            existing_history,
            completed_course_ids,
            course_map,
        )
        total_points = sum(int(item.get("points", 0) or 0) for item in points_history)
        level_data = StudentPerformanceCRUD._update_level_system(
            {"xp": total_points, "level": 1}
        )

        course_stats = sorted(
            course_stats_map.values(),
            key=lambda item: StudentPerformanceCRUD._coerce_datetime(item.get("lastActive")) or datetime.min,
            reverse=True,
        )

        await COL.update_one(
            _query(student_id, None),
            {
                "$set": {
                    "studentName": raw_perf.get("studentName", "Student"),
                    "userId": raw_perf.get("userId"),
                    "courseStats": course_stats,
                    "pointsHistory": points_history,
                    "totalPoints": total_points,
                    "pointsThisWeek": StudentPerformanceCRUD._compute_points_this_week(points_history),
                    "xp": level_data["xp"],
                    "level": level_data["level"],
                    "xpToNextLevel": level_data["xpToNextLevel"],
                }
            },
        )

        global_doc = await COL.find_one(_query(student_id, None)) or raw_perf
        filtered_certificates = build_filtered_certificates(
            global_doc.get("certificates", []) or [],
            completed_course_ids,
            course_map,
            CERTIFICATE_TEMPLATE_VERSION,
        )

        await COL.update_one(
            _query(student_id, None),
            {"$set": {"certificates": filtered_certificates}},
        )

        synced = await COL.find_one(_query(student_id, None))
        return fix_object_ids(synced) if synced else None

    @staticmethod
    async def create_performance_record(
        student_id: str, student_name: str, tenant_id: str, user_id: str = None
    ):
        await COL.insert_one(
            {
                "studentId": safe_oid(student_id),
                "studentName": student_name,
                "tenantId": safe_oid(tenant_id),
                "userId": safe_oid(user_id) if user_id else None,
                "totalPoints": 0,
                "pointsThisWeek": 0,
                "xp": 0,
                "level": 1,
                "xpToNextLevel": 300,
                "badges": [],
                "pointsHistory": [],
                "certificates": [],
                "weeklyStudyTime": [],
                "courseStats": [],
                "createdAt": datetime.utcnow(),
            }
        )
        return True

    @staticmethod
    def _update_level_system(data: dict):
        return update_level_system(data)

    @staticmethod
    async def get_student_performance(student_id: str, tenant_id: str | None):
        doc = await StudentPerformanceCRUD.sync_from_progress(student_id, tenant_id)
        if not doc:
            return None
        doc["id"] = doc.get("_id")
        return doc

    @staticmethod
    async def get_global_student_performance(student_id: str):
        doc = await StudentPerformanceCRUD.sync_global_from_progress(student_id)
        if not doc:
            return None
        doc["id"] = doc.get("_id")
        return doc

    @staticmethod
    async def add_points(student_id: str, tenant_id: str, points: int, reason: str = "Course Activity", course_id: str | None = None):
        history_item = {"points": points, "reason": reason, "date": datetime.utcnow()}
        if course_id:
            history_item["courseId"] = course_id
        await COL.update_one(
            _query(student_id, tenant_id),
            {
                "$inc": {"totalPoints": points, "pointsThisWeek": points, "xp": points},
                "$push": {"pointsHistory": history_item}
            },
        )
        updated = await StudentPerformanceCRUD.get_student_performance(
            student_id, tenant_id
        )
        updated = StudentPerformanceCRUD._update_level_system(updated)
        await COL.update_one(
            _query(student_id, tenant_id),
            {
                "$set": {
                    "xp": updated["xp"],
                    "level": updated["level"],
                    "xpToNextLevel": updated["xpToNextLevel"],
                }
            },
        )
        return updated

    @staticmethod
    async def add_badge(student_id: str, tenant_id: str, badge: dict):
        badge["date"] = datetime.utcnow()
        await COL.update_one(
            _query(student_id, tenant_id), {"$push": {"badges": badge}}
        )
        return await StudentPerformanceCRUD.get_student_performance(
            student_id, tenant_id
        )

    @staticmethod
    async def view_badges(student_id: str, tenant_id: str):
        doc = await COL.find_one(_query(student_id, tenant_id), {"badges": 1, "_id": 0})
        return fix_object_ids(doc.get("badges", [])) if doc else []

    @staticmethod
    async def add_certificate(student_id: str, tenant_id: str, cert: dict):
        cert["date"] = datetime.utcnow()
        await COL.update_one(
            _query(student_id, tenant_id), {"$push": {"certificates": cert}}
        )
        return await StudentPerformanceCRUD.get_student_performance(
            student_id, tenant_id
        )

    @staticmethod
    async def view_certificates(student_id: str, tenant_id: str):
        doc = await COL.find_one(
            _query(student_id, tenant_id), {"certificates": 1, "_id": 0}
        )
        return fix_object_ids(doc.get("certificates", [])) if doc else []

    @staticmethod
    async def get_course_stats(student_id: str, tenant_id: str):
        doc = await COL.find_one(
            _query(student_id, tenant_id), {"courseStats": 1, "_id": 0}
        )
        return fix_object_ids(doc.get("courseStats", [])) if doc else []

    @staticmethod
    async def update_course_progress(
        student_id: str,
        tenant_id: str,
        course_id: str,
        completion: int,
        last_active: str,
    ):
        q = _query(student_id, tenant_id)
        res = await COL.update_one(
            {**q, "courseStats.courseId": course_id},
            {
                "$set": {
                    "courseStats.$.completionPercentage": completion,
                    "courseStats.$.lastActive": last_active,
                }
            },
        )
        if res.modified_count == 0:
            await COL.update_one(
                q,
                {
                    "$push": {
                        "courseStats": {
                            "courseId": course_id,
                            "completionPercentage": completion,
                            "lastActive": last_active,
                        }
                    }
                },
            )
        if completion == 100:
            exists = await COL.find_one({**q, "certificates.courseId": course_id})
            if not exists:
                from app.db.database import courses_collection
                
                course_doc = await courses_collection.find_one({"_id": safe_oid(course_id)})
                student_doc = await COL.find_one(q)
                
                course_name = course_doc.get("title", "Unknown Course") if course_doc else "Unknown Course"
                student_name = student_doc.get("studentName", "Student") if student_doc else "Student"

                await StudentPerformanceCRUD._ensure_course_certificate(
                    student_id=student_id,
                    tenant_id=tenant_id,
                    course_id=course_id,
                    course_name=course_name,
                    student_name=student_name,
                )
        return await StudentPerformanceCRUD.get_student_performance(
            student_id, tenant_id
        )

    @staticmethod
    async def add_weekly_time(
        student_id: str, tenant_id: str, week_start: str, minutes: int
    ):
        await COL.update_one(
            _query(student_id, tenant_id),
            {
                "$push": {
                    "weeklyStudyTime": {"weekStart": week_start, "minutes": minutes}
                }
            },
        )
        return await StudentPerformanceCRUD.get_student_performance(
            student_id, tenant_id
        )

    @staticmethod
    async def _get_leaderboard(pipeline: list):
        pipeline.extend(
            [
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "userId",
                        "foreignField": "_id",
                        "as": "user",
                    }
                },
                {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
                {"$sort": {"totalPoints": -1}},
            ]
        )
        docs = await COL.aggregate(pipeline).to_list(length=None)
        return collapse_leaderboard_docs(docs)

    @staticmethod
    async def _ranked(pipeline, limit=None):
        lb = await StudentPerformanceCRUD._get_leaderboard(pipeline)
        items = lb[:limit] if limit else lb
        for i, item in enumerate(items, 1):
            item["rank"] = i
        return items

    @staticmethod
    async def tenant_top5(tenant_id: str):
        return await StudentPerformanceCRUD._ranked(
            [{"$match": {"tenantId": ObjectId(tenant_id)}}], 5
        )

    @staticmethod
    async def tenant_full(tenant_id: str):
        return await StudentPerformanceCRUD._ranked(
            [{"$match": {"tenantId": ObjectId(tenant_id)}}]
        )

    @staticmethod
    async def global_top5():
        return await StudentPerformanceCRUD._ranked([], 5)

    @staticmethod
    async def global_full():
        return await StudentPerformanceCRUD._ranked([])

    @staticmethod
    async def global_summary(student_id: str | None = None, limit: int = 10):
        lb = await StudentPerformanceCRUD._ranked([])
        current_student = None
        if student_id:
            current_student = next(
                (item for item in lb if item.get("studentId") == student_id),
                None,
            )
        return {
            "top": lb[:limit],
            "currentStudent": current_student,
            "totalStudents": len(lb),
        }

    @staticmethod
    async def get_teacher_performances(teacher_id: str, tenant_id: str):
        from app.crud._teacher_perf_pipeline import run_teacher_perf_pipeline

        return await run_teacher_perf_pipeline(teacher_id, tenant_id)
