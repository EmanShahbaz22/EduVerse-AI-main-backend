"""Quiz analytics: summary, student analytics, teacher dashboard."""

from bson import ObjectId
from typing import Optional
from app.db.database import db


async def get_quiz_summary(quiz_id: str, top_n: int = 5):
    q_oid = ObjectId(quiz_id)
    graded = {"quizId": q_oid, "status": "graded"}

    basic = await db.quizSubmissions.aggregate(
        [
            {"$match": graded},
            {
                "$group": {
                    "_id": None,
                    "totalAttempts": {"$sum": 1},
                    "avgPercentage": {"$avg": "$percentage"},
                    "avgMarks": {"$avg": "$obtainedMarks"},
                }
            },
        ]
    ).to_list(length=1)
    stats = (
        basic[0]
        if basic
        else {"totalAttempts": 0, "avgPercentage": None, "avgMarks": None}
    )

    top_list = [
        {
            "studentId": str(d["studentId"]),
            "obtainedMarks": d.get("obtainedMarks"),
            "percentage": d.get("percentage"),
        }
        async for d in db.quizSubmissions.find(
            graded, {"studentId": 1, "obtainedMarks": 1, "percentage": 1}
        )
        .sort("obtainedMarks", -1)
        .limit(top_n)
    ]

    pass_res = await db.quizSubmissions.aggregate(
        [
            {"$match": graded},
            {
                "$group": {
                    "_id": None,
                    "passCount": {
                        "$sum": {"$cond": [{"$gte": ["$percentage", 50]}, 1, 0]}
                    },
                    "total": {"$sum": 1},
                }
            },
        ]
    ).to_list(length=1)
    ps = pass_res[0] if pass_res else {"passCount": 0, "total": 0}
    pass_rate = (ps["passCount"] / ps["total"] * 100) if ps["total"] else 0.0

    buckets = await db.quizSubmissions.aggregate(
        [
            {"$match": {**graded, "percentage": {"$ne": None}}},
            {
                "$bucket": {
                    "groupBy": "$percentage",
                    "boundaries": list(range(0, 101, 10)),
                    "default": "100+",
                    "output": {"count": {"$sum": 1}},
                }
            },
        ]
    ).to_list(length=20)

    return {
        **stats,
        "topScores": top_list,
        "passRate": pass_rate,
        "distribution": {str(b["_id"]): b["count"] for b in buckets},
    }


async def get_student_analytics(student_id: str, recent: int = 5):
    s_oid = ObjectId(student_id)
    agg = await db.quizSubmissions.aggregate(
        [
            {"$match": {"studentId": s_oid, "status": "graded"}},
            {
                "$group": {
                    "_id": "$studentId",
                    "totalTaken": {"$sum": 1},
                    "avgPercentage": {"$avg": "$percentage"},
                }
            },
        ]
    ).to_list(length=1)
    stats = agg[0] if agg else {"totalTaken": 0, "avgPercentage": None}

    recent_list = [
        {
            "quizId": str(d["quizId"]),
            "percentage": d.get("percentage"),
            "submittedAt": d.get("submittedAt"),
        }
        async for d in db.quizSubmissions.find(
            {"studentId": s_oid, "status": "graded"},
            {"quizId": 1, "percentage": 1, "submittedAt": 1},
        )
        .sort("submittedAt", -1)
        .limit(recent)
    ]
    return {
        "totalTaken": stats.get("totalTaken", 0),
        "avgPercentage": stats.get("avgPercentage"),
        "recentAttempts": recent_list,
    }


async def get_teacher_dashboard(teacher_id: str, course_id: Optional[str] = None):
    quiz_query = {"teacherId": ObjectId(teacher_id)}
    if course_id:
        quiz_query["courseId"] = ObjectId(course_id)

    quiz_list, quiz_ids = [], []
    async for q in db.quizzes.find(
        quiz_query, {"_id": 1, "quizNumber": 1, "courseId": 1}
    ):
        quiz_ids.append(q["_id"])
        quiz_list.append(
            {
                "quizId": str(q["_id"]),
                "quizNumber": q.get("quizNumber"),
                "courseId": str(q.get("courseId")),
            }
        )
    if not quiz_ids:
        return {"quizzes": [], "pendingSubmissions": 0}

    agg_results = await db.quizSubmissions.aggregate(
        [
            {"$match": {"quizId": {"$in": quiz_ids}, "status": "graded"}},
            {
                "$group": {
                    "_id": "$quizId",
                    "attempts": {"$sum": 1},
                    "avgPercentage": {"$avg": "$percentage"},
                    "passCount": {
                        "$sum": {"$cond": [{"$gte": ["$percentage", 50]}, 1, 0]}
                    },
                }
            },
        ]
    ).to_list(length=len(quiz_ids))
    agg_map = {str(r["_id"]): r for r in agg_results}

    pending = await db.quizSubmissions.count_documents(
        {"quizId": {"$in": quiz_ids}, "status": {"$ne": "graded"}}
    )

    summary = []
    for q in quiz_list:
        s = agg_map.get(q["quizId"])
        attempts = s["attempts"] if s else 0
        summary.append(
            {
                **q,
                "attempts": attempts,
                "avgPercentage": s["avgPercentage"] if s else None,
                "passRate": (s["passCount"] / attempts * 100)
                if s and attempts
                else 0.0,
            }
        )
    return {"quizzes": summary, "pendingSubmissions": pending}
