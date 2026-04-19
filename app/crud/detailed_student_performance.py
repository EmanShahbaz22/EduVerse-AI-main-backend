from bson import ObjectId

from app.db.database import (
    ai_quiz_sessions_collection,
    courses_collection,
    quiz_submissions_collection,
    quizzes_collection,
    students_collection,
    users_collection,
)


def _normalize_id(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _format_score_display(score, total: int) -> str:
    if isinstance(score, (int, float)):
        return f"{score}/{total}"
    return str(score)


async def get_detailed_student_performance(teacher_id: str, student_id: str, tenant_id: str):
    """
    Fetch detailed quiz scores for a student, limited to courses
    taught by the requested teacher inside the same tenant.
    """
    if not ObjectId.is_valid(tenant_id) or not ObjectId.is_valid(student_id):
      return []

    tenant_oid = ObjectId(tenant_id)
    teacher_oid = ObjectId(teacher_id) if ObjectId.is_valid(teacher_id) else None

    teacher_query = {
        "tenantId": tenant_oid,
        "$or": [
            {"teacherId": teacher_id},
            {"teacherId": teacher_oid} if teacher_oid else {"teacherId": None},
        ],
    }

    courses = await courses_collection.find(teacher_query).to_list(length=None)
    if not courses:
        return []

    course_ids = [str(course["_id"]) for course in courses]
    course_ids_set = set(course_ids)

    student = await students_collection.find_one({"_id": ObjectId(student_id)})
    if not student:
        return []

    enrolled_courses = {
        str(course_id)
        for course_id in student.get("enrolledCourses", [])
        if _normalize_id(course_id) in course_ids_set
    }

    if not enrolled_courses:
        return []

    student_name = student.get("studentName", "Unknown Student")
    if student.get("userId"):
        user = await users_collection.find_one({"_id": student["userId"]})
        if user:
            student_name = user.get("fullName", student_name)

    quizzes = await quizzes_collection.find(
        {"courseId": {"$in": list(enrolled_courses)}, "tenantId": tenant_oid}
    ).to_list(length=None)
    ai_quizzes = await ai_quiz_sessions_collection.find(
        {
            "studentId": student_id,
            "courseId": {"$in": list(enrolled_courses)},
        }
    ).to_list(length=None)

    quiz_ids = [str(item["_id"]) for item in quizzes] + [str(item["_id"]) for item in ai_quizzes]

    quiz_submissions = await quiz_submissions_collection.find(
        {
            "studentId": student_id,
            "courseId": {"$in": list(enrolled_courses)},
            "quizId": {"$in": quiz_ids},
        }
    ).to_list(length=None)

    quizzes_by_course: dict[str, list[dict]] = {}

    quiz_submission_map = {
        str(submission.get("quizId")): submission
        for submission in quiz_submissions
    }

    for quiz in quizzes:
        course_id = str(quiz.get("courseId"))
        quiz_id = str(quiz["_id"])
        submission = quiz_submission_map.get(quiz_id)
        score_value = "Pending"
        if submission and submission.get("obtainedMarks") is not None:
            score_value = submission.get("obtainedMarks")
        elif submission and submission.get("percentage") is not None:
            score_value = submission.get("percentage")
        elif submission:
            score_value = "Ungraded"

        total_marks = quiz.get("totalMarks", 100)
        title = quiz.get("title") or (
            f"Quiz {quiz.get('quizNumber')}" if quiz.get("quizNumber") else "Quiz"
        )
        quizzes_by_course.setdefault(course_id, []).append(
            {
                "id": quiz_id,
                "title": title,
                "score": score_value,
                "total": total_marks,
                "scoreDisplay": _format_score_display(score_value, total_marks),
            }
        )

    for quiz in ai_quizzes:
        course_id = str(quiz.get("courseId"))
        quiz_id = str(quiz["_id"])
        submission = quiz_submission_map.get(quiz_id)
        score_value = "Pending"
        if submission and submission.get("obtainedMarks") is not None:
            score_value = submission.get("obtainedMarks")
        elif submission and submission.get("percentage") is not None:
            score_value = submission.get("percentage")
        elif submission:
            score_value = "Ungraded"

        questions = quiz.get("questions") or []
        total_marks = quiz.get("totalMarks", len(questions) or 100)
        quizzes_by_course.setdefault(course_id, []).append(
            {
                "id": quiz_id,
                "title": quiz.get("topic") or "AI Quiz",
                "score": score_value,
                "total": total_marks,
                "scoreDisplay": _format_score_display(score_value, total_marks),
            }
        )

    results = []
    for course in courses:
        course_id = str(course["_id"])
        if course_id not in enrolled_courses:
            continue

        results.append(
            {
                "courseId": course_id,
                "courseName": course.get("title", "Unknown Course"),
                "studentName": student_name,
                "quizzes": quizzes_by_course.get(course_id, []),
            }
        )

    return results
