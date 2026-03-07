"""Extracted teacher performance aggregation pipeline (large MongoDB pipeline)."""

from bson import ObjectId


async def run_teacher_perf_pipeline(teacher_id: str, tenant_id: str):
    from app.db.database import courses_collection, students_collection

    try:
        toid = ObjectId(tenant_id)
        teacher_query = {
            "tenantId": toid,
            "$or": [
                {"teacherId": teacher_id},
                {
                    "teacherId": ObjectId(teacher_id)
                    if ObjectId.is_valid(teacher_id)
                    else None
                },
            ],
        }
        courses = await courses_collection.find(teacher_query).to_list(length=None)
        course_ids = [str(c["_id"]) for c in courses]
        if not course_ids:
            return []

        _filter = lambda field: {
            "$let": {
                "vars": {
                    "matched": {
                        "$filter": {
                            "input": {"$ifNull": ["$performance.courseStats", []]},
                            "as": "s",
                            "cond": {"$eq": ["$$s.courseId", "$enrolledCourses"]},
                        }
                    }
                },
                "in": {
                    "$ifNull": [
                        {"$arrayElemAt": [f"$$matched.{field}", 0]},
                        0 if field == "completionPercentage" else "Never",
                    ]
                },
            }
        }

        pipeline = [
            {"$match": {"tenantId": toid, "enrolledCourses": {"$in": course_ids}}},
            {"$unwind": "$enrolledCourses"},
            {"$match": {"enrolledCourses": {"$in": course_ids}}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "userId",
                    "foreignField": "_id",
                    "as": "user",
                }
            },
            {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
            {"$addFields": {"course_oid": {"$toObjectId": "$enrolledCourses"}}},
            {
                "$lookup": {
                    "from": "courses",
                    "localField": "course_oid",
                    "foreignField": "_id",
                    "as": "course",
                }
            },
            {"$unwind": {"path": "$course", "preserveNullAndEmptyArrays": True}},
            {
                "$lookup": {
                    "from": "studentPerformance",
                    "localField": "_id",
                    "foreignField": "studentId",
                    "as": "performance",
                }
            },
            {"$unwind": {"path": "$performance", "preserveNullAndEmptyArrays": True}},
            {
                "$project": {
                    "_id": {"$ifNull": [{"$toString": "$performance._id"}, ""]},
                    "studentId": {"$toString": "$_id"},
                    "courseId": "$enrolledCourses",
                    "tenantId": {"$toString": "$tenantId"},
                    "studentName": {
                        "$ifNull": ["$user.fullName", "$studentName", "Unknown"]
                    },
                    "courseName": {"$ifNull": ["$course.title", "Unknown Course"]},
                    "progress": _filter("completionPercentage"),
                    "lastUpdated": _filter("lastActive"),
                    "marks": {"$literal": 0},
                    "totalMarks": {"$literal": 0},
                    "grade": {"$literal": "N/A"},
                    "attendance": {"$literal": 100},
                }
            },
        ]
        return await students_collection.aggregate(pipeline).to_list(length=None)
    except Exception:
        return []
