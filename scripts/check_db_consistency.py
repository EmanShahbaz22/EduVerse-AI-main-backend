import asyncio
from collections import defaultdict
import os
import sys
from pathlib import Path

from bson import ObjectId

# Allow running as: `python scripts/check_db_consistency.py`
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.database import db


async def _id_set(collection_name: str) -> set[ObjectId]:
    return {doc["_id"] async for doc in db[collection_name].find({}, {"_id": 1})}


async def main():
    timeout_seconds = int(os.getenv("DB_CHECK_TIMEOUT_SECONDS", "20"))
    try:
        await asyncio.wait_for(db.command("ping"), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        print(
            f"DB consistency check aborted: MongoDB connection timed out after {timeout_seconds}s."
        )
        raise SystemExit(2)
    except Exception as exc:
        print(f"DB consistency check aborted: MongoDB connection failed: {exc}")
        raise SystemExit(2)

    issues = defaultdict(list)

    users = {doc["_id"]: doc async for doc in db.users.find({})}
    students = [doc async for doc in db.students.find({})]
    teachers = [doc async for doc in db.teachers.find({})]
    admins = [doc async for doc in db.admins.find({})]
    courses = {doc["_id"]: doc async for doc in db.courses.find({})}
    quizzes = {doc["_id"]: doc async for doc in db.quizzes.find({})}
    students_by_user = {s.get("userId"): s for s in students if s.get("userId")}

    for s in students:
        uid = s.get("userId")
        user = users.get(uid)
        if not user:
            issues["students_missing_user"].append(str(s["_id"]))
            continue
        if user.get("role") != "student":
            issues["students_role_mismatch"].append(str(s["_id"]))
        student_tid = s.get("tenantId")
        user_tid = user.get("tenantId")
        if student_tid and user_tid and student_tid != user_tid:
            issues["students_user_tenant_mismatch"].append(str(s["_id"]))

    for uid, user in users.items():
        if user.get("role") == "student" and uid not in students_by_user:
            issues["users_missing_student_profile"].append(str(uid))

    for t in teachers:
        uid = t.get("userId")
        user = users.get(uid)
        if not user:
            issues["teachers_missing_user"].append(str(t["_id"]))
            continue
        if user.get("role") != "teacher":
            issues["teachers_role_mismatch"].append(str(t["_id"]))

    for a in admins:
        uid = a.get("userId")
        user = users.get(uid)
        if not user:
            issues["admins_missing_user"].append(str(a["_id"]))
            continue
        if user.get("role") != "admin":
            issues["admins_role_mismatch"].append(str(a["_id"]))

    for s in students:
        for cid in s.get("enrolledCourses", []):
            if not ObjectId.is_valid(cid):
                issues["students_invalid_course_id"].append(
                    f"{s['_id']}:invalid:{cid}"
                )
                continue
            if ObjectId(cid) not in courses:
                issues["students_missing_course_ref"].append(f"{s['_id']}:{cid}")

    async for sub in db.quizSubmissions.find({}):
        sid = sub.get("studentId")
        qid = sub.get("quizId")
        cid = sub.get("courseId")
        tid = sub.get("tenantId")

        student = next((s for s in students if s["_id"] == sid), None)
        if not student:
            issues["quiz_sub_missing_student"].append(str(sub["_id"]))
            continue
        student_tid = student.get("tenantId")
        if student_tid and tid and tid != student_tid:
            issues["quiz_sub_tenant_student_mismatch"].append(str(sub["_id"]))

        quiz = quizzes.get(qid)
        if not quiz:
            issues["quiz_sub_missing_quiz"].append(str(sub["_id"]))
        elif not tid or quiz.get("tenantId") != tid:
            issues["quiz_sub_tenant_quiz_mismatch"].append(str(sub["_id"]))

        course = courses.get(cid)
        if not course:
            issues["quiz_sub_missing_course"].append(str(sub["_id"]))

    if not issues:
        print("DB consistency check passed (no issues found).")
        return

    print("DB consistency issues detected:")
    for name, values in issues.items():
        print(f"- {name}: {len(values)}")
        for value in values[:20]:
            print(f"  - {value}")
        if len(values) > 20:
            print("  - ...")
    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
