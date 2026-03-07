import argparse
import asyncio
from datetime import datetime
import sys
from pathlib import Path

from bson import ObjectId

# Allow running as: `python scripts/repair_db_consistency.py`
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.database import db


async def _collect_issues():
    users = {doc["_id"]: doc async for doc in db.users.find({})}
    students = [doc async for doc in db.students.find({})]
    teachers = [doc async for doc in db.teachers.find({})]
    courses = {doc["_id"]: doc async for doc in db.courses.find({}, {"_id": 1})}
    students_by_user = {s.get("userId"): s for s in students if s.get("userId")}

    students_missing_user = []
    for s in students:
        if s.get("userId") not in users:
            students_missing_user.append(s["_id"])

    users_missing_student_profile = []
    for uid, user in users.items():
        if user.get("role") == "student" and uid not in students_by_user:
            users_missing_student_profile.append(uid)

    teachers_missing_user = []
    for t in teachers:
        if t.get("userId") not in users:
            teachers_missing_user.append(t["_id"])

    quiz_sub_missing_student = []
    quiz_sub_missing_course = []
    student_ids = {s["_id"] for s in students}
    async for sub in db.quizSubmissions.find({}, {"studentId": 1, "courseId": 1}):
        sid = sub.get("studentId")
        cid = sub.get("courseId")
        if sid not in student_ids:
            quiz_sub_missing_student.append(sub["_id"])
            continue
        if cid not in courses:
            quiz_sub_missing_course.append(sub["_id"])

    return {
        "students_missing_user": students_missing_user,
        "users_missing_student_profile": users_missing_student_profile,
        "teachers_missing_user": teachers_missing_user,
        "quiz_sub_missing_student": quiz_sub_missing_student,
        "quiz_sub_missing_course": quiz_sub_missing_course,
    }


async def _repair(issues: dict[str, list[ObjectId]], apply: bool):
    plan = {
        "delete_orphan_students": len(issues["students_missing_user"]),
        "create_missing_student_profiles": len(issues["users_missing_student_profile"]),
        "delete_orphan_teachers": len(issues["teachers_missing_user"]),
        "delete_orphan_quiz_sub_by_student": len(issues["quiz_sub_missing_student"]),
        "delete_orphan_quiz_sub_by_course": len(issues["quiz_sub_missing_course"]),
    }

    print("Repair plan:")
    for k, v in plan.items():
        print(f"- {k}: {v}")

    if not apply:
        print("Dry run only. Re-run with --apply to execute changes.")
        return

    now = datetime.utcnow()

    # 1) Remove orphan student docs + linked performance/submissions.
    if issues["students_missing_user"]:
        student_ids = issues["students_missing_user"]
        await db.students.delete_many({"_id": {"$in": student_ids}})
        await db.studentPerformance.delete_many({"studentId": {"$in": student_ids}})
        await db.quizSubmissions.delete_many({"studentId": {"$in": student_ids}})

    # 2) Create missing student profiles for role=student users.
    for uid in issues["users_missing_student_profile"]:
        user = await db.users.find_one({"_id": uid})
        if not user:
            continue
        tenant_id = user.get("tenantId")
        student_doc = {
            "userId": uid,
            "enrolledCourses": [],
            "completedCourses": [],
            "createdAt": now,
            "updatedAt": now,
        }
        if tenant_id:
            student_doc["tenantId"] = tenant_id

        res = await db.students.insert_one(student_doc)
        student_id = res.inserted_id

        perf_doc = {
            "studentId": student_id,
            "userId": uid,
            "studentName": user.get("fullName", "Student"),
            "totalPoints": 0,
            "pointsThisWeek": 0,
            "xp": 0,
            "level": 1,
            "xpToNextLevel": 300,
            "badges": [],
            "certificates": [],
            "weeklyStudyTime": [],
            "courseStats": [],
            "createdAt": now,
            "updatedAt": now,
        }
        if tenant_id:
            perf_doc["tenantId"] = tenant_id

        await db.studentPerformance.insert_one(perf_doc)

    # 3) Remove orphan teacher docs.
    if issues["teachers_missing_user"]:
        await db.teachers.delete_many({"_id": {"$in": issues["teachers_missing_user"]}})

    # 4) Remove orphan quiz submissions.
    quiz_sub_orphans = list(
        {
            *issues["quiz_sub_missing_student"],
            *issues["quiz_sub_missing_course"],
        }
    )
    if quiz_sub_orphans:
        await db.quizSubmissions.delete_many({"_id": {"$in": quiz_sub_orphans}})

    print("Repair applied successfully.")


async def main():
    parser = argparse.ArgumentParser(
        description="Repair common referential consistency issues in MongoDB."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply repairs. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="MongoDB ping timeout in seconds.",
    )
    args = parser.parse_args()

    try:
        await asyncio.wait_for(db.command("ping"), timeout=args.timeout)
    except asyncio.TimeoutError:
        print(f"Repair aborted: MongoDB connection timed out after {args.timeout}s.")
        raise SystemExit(2)
    except Exception as exc:
        print(f"Repair aborted: MongoDB connection failed: {exc}")
        raise SystemExit(2)

    issues = await _collect_issues()
    await _repair(issues, apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
