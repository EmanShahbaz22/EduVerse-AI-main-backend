from bson import ObjectId
from datetime import datetime
from app.db.database import student_performance_collection
from app.utils.mongo import fix_object_ids

COL = student_performance_collection


def _query(student_id: str, tenant_id: str):
    return {"studentId": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}


class StudentPerformanceCRUD:
    @staticmethod
    async def create_performance_record(
        student_id: str, student_name: str, tenant_id: str, user_id: str = None
    ):
        await COL.insert_one(
            {
                "studentId": ObjectId(student_id),
                "studentName": student_name,
                "tenantId": ObjectId(tenant_id),
                "userId": ObjectId(user_id) if user_id else None,
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
        return True

    @staticmethod
    def _update_level_system(data: dict):
        xp, level = data.get("xp", 0), data.get("level", 1)
        xp_needed = lambda lv: int(round(300 * (1.5 ** (lv - 1)) / 50) * 50)
        req = xp_needed(level)
        while xp >= req:
            xp -= req
            level += 1
            req = xp_needed(level)
        data.update({"xp": xp, "level": level, "xpToNextLevel": req})
        return data

    @staticmethod
    async def get_student_performance(student_id: str, tenant_id: str):
        doc = await COL.find_one(_query(student_id, tenant_id))
        if not doc:
            return None
        doc = fix_object_ids(doc)
        doc["id"] = doc.get("_id")
        return doc

    @staticmethod
    async def add_points(student_id: str, tenant_id: str, points: int):
        await COL.update_one(
            _query(student_id, tenant_id),
            {"$inc": {"totalPoints": points, "pointsThisWeek": points, "xp": points}},
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
            exists = await COL.find_one({**q, "badges.courseId": course_id})
            if not exists:
                await StudentPerformanceCRUD.add_badge(
                    student_id,
                    tenant_id,
                    {
                        "courseId": course_id,
                        "name": "Course Completer",
                        "icon": "completion.png",
                    },
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
        lb = [
            {
                "studentName": (d.get("user", {}) or {}).get("fullName")
                or d.get("studentName"),
                "points": d.get("totalPoints", 0),
            }
            for d in docs
        ]
        lb.sort(key=lambda x: -x["points"])
        return lb

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
    async def get_teacher_performances(teacher_id: str, tenant_id: str):
        from app.crud._teacher_perf_pipeline import run_teacher_perf_pipeline

        return await run_teacher_perf_pipeline(teacher_id, tenant_id)
