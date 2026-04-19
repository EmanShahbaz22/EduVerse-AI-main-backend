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
            {"$match": {"enrolledCourses": {"$in": course_ids}}},
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
                    "grade": {"$literal": "N/A"}
                }
            },
        ]
        
        raw_results = await students_collection.aggregate(pipeline).to_list(length=None)
        
        if not raw_results:
            return []

        # Optimization: Fetch all assessments for these courses, including AI quiz sessions
        from app.db.database import (
            ai_quiz_sessions_collection,
            quizzes_collection,
            quiz_submissions_collection,
        )
        from app.crud.grade_calculator import calculate_grade
        
        # 1. Get all potential points per course
        quizzes = await quizzes_collection.find({"courseId": {"$in": course_ids}, "tenantId": toid}).to_list(None)
        ai_quizzes = await ai_quiz_sessions_collection.find({"courseId": {"$in": course_ids}}).to_list(None)
        
        course_totals = {}
        ai_totals = {}
        for q in quizzes:
            cid = str(q["courseId"])
            course_totals[cid] = course_totals.get(cid, 0) + q.get("totalMarks", 100)

        for q in ai_quizzes:
            cid = str(q.get("courseId"))
            sid = str(q.get("studentId"))
            if not cid or not sid:
                continue
            questions = q.get("questions") or []
            total_marks = q.get("totalMarks") or len(questions) or 0
            key = (cid, sid)
            ai_totals[key] = ai_totals.get(key, 0) + total_marks
            
        # 2. Extract student IDs efficiently
        student_ids = list(set([str(r["studentId"]) for r in raw_results]))
        
        # 3. Fetch submissions
        q_subs = await quiz_submissions_collection.find({"courseId": {"$in": course_ids}, "studentId": {"$in": student_ids}}).to_list(None)
        
        # map: (courseId, studentId) -> earned_score
        earned_map = {}
        for sub in q_subs:
            key = (str(sub["courseId"]), str(sub["studentId"]))
            val = sub.get("obtainedMarks")
            if val is None:
                val = sub.get("percentage")
            if val is not None:
                earned_map[key] = earned_map.get(key, 0) + val
            
        # 4. Post-process the results
        for r in raw_results:
            c_id = r["courseId"]
            s_id = r["studentId"]
            
            total_possible = course_totals.get(c_id, 0) + ai_totals.get((c_id, s_id), 0)
            earned = earned_map.get((c_id, s_id), 0)
            
            r["marks"] = earned
            r["totalMarks"] = total_possible
            r["grade"] = calculate_grade(earned, total_possible)
            
        return raw_results
    except Exception:
        return []
