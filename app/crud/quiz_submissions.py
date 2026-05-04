from bson import ObjectId
from datetime import datetime
from app.db.database import db
from typing import Optional, Tuple
from app.services.quiz_generator import normalize_quiz_questions


def serialize_submission(s: dict) -> dict:
    return {
        "id": str(s["_id"]),
        "studentId": str(s["studentId"]),
        "quizId": str(s["quizId"]),
        "courseId": str(s["courseId"]),
        "tenantId": str(s["tenantId"]),
        "submittedAt": s["submittedAt"],
        "answers": s.get("answers", []),
        "percentage": s.get("percentage"),
        "obtainedMarks": s.get("obtainedMarks"),
        "status": s.get("status", "pending"),
    }


def _grade_submission(
    quiz_doc: dict, submission_doc: dict
) -> Tuple[float, float, list]:
    questions = normalize_quiz_questions(quiz_doc.get("questions", []))
    total_marks = quiz_doc.get("totalMarks", len(questions)) or len(questions)

    has_explicit = any(
        isinstance(q, dict) and q.get("marks") is not None for q in questions
    )
    if has_explicit:
        marks_per_q = [float(q.get("marks", 1)) for q in questions]
    else:
        per_q = float(total_marks) / max(len(questions), 1)
        marks_per_q = [per_q] * len(questions)

    answer_map = {
        a["questionIndex"]: a["selected"] for a in submission_doc.get("answers", [])
    }
    obtained, details = 0.0, []

    for idx, q in enumerate(questions):
        correct = None
        if isinstance(q, dict):
            # Support both legacy quizzes (answer) and AI sessions (correctAnswer)
            correct = q.get("answer") or q.get("correctAnswer")
        selected = answer_map.get(idx)
        q_marks = marks_per_q[idx] if idx < len(marks_per_q) else 0.0
        is_correct = selected is not None and selected == correct
        awarded = q_marks if is_correct else 0.0
        obtained += awarded
        details.append(
            {
                "questionIndex": idx,
                "selected": selected,
                "correctAnswer": correct,
                "isCorrect": is_correct,
                "awardedMarks": awarded,
                "possibleMarks": q_marks,
            }
        )
    return obtained, total_marks, details


async def submit_and_grade_submission(payload, *, student_id: str, tenant_id: str):
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    raw_answers = data.pop("answers", [])
    normalised_answers = []
    for idx, ans in enumerate(raw_answers):
        if isinstance(ans, dict):
            qi = ans.get("questionIndex", idx)
            sel = ans.get("selected") or ans.get("answer")
        else:
            qi = idx
            sel = ans
        normalised_answers.append(
            {"questionIndex": int(qi), "selected": str(sel) if sel is not None else None}
        )
    data["answers"] = normalised_answers

    tenant_oid = ObjectId(tenant_id) if tenant_id and ObjectId.is_valid(str(tenant_id)) else None
    data.update(
        {
            "studentId": ObjectId(student_id),
            "quizId": ObjectId(data["quizId"]),
            "courseId": ObjectId(data["courseId"]),
            "tenantId": tenant_oid,
            "submittedAt": datetime.utcnow(),
            "status": "pending",
        }
    )

    if await db.quizSubmissions.find_one(
        {
            "studentId": data["studentId"],
            "quizId": data["quizId"],
            "tenantId": data["tenantId"],
        }
    ):
        return "AlreadySubmitted"

    quiz = await db.quizzes.find_one({"_id": data["quizId"], "tenantId": data["tenantId"]})
    from app.db.database import ai_quiz_sessions_collection
    is_ai_quiz = False
    
    if not quiz:
        # Fallback to AI Quiz Sessions
        quiz = await ai_quiz_sessions_collection.find_one({"_id": data["quizId"]})
        if quiz:
            is_ai_quiz = True
            # For AI quizzes, we might need to verify student ownership
            if str(quiz.get("studentId")) != student_id:
                return None
            # If tenant is missing, derive from course to keep submissions scoped
            if not data.get("tenantId"):
                course = await db.courses.find_one({"_id": data["courseId"]})
                if course and course.get("tenantId"):
                    data["tenantId"] = course.get("tenantId")
        else:
            return None

    res = await db.quizSubmissions.insert_one(data)
    submission_doc = await db.quizSubmissions.find_one({"_id": res.inserted_id})

    obtained, total, details = _grade_submission(quiz, submission_doc)
    pct = round((obtained / total) * 100, 2) if total > 0 else 0.0

    await db.quizSubmissions.update_one(
        {"_id": res.inserted_id},
        {
            "$set": {
                "obtainedMarks": obtained,
                "percentage": pct,
                "status": "graded",
                "gradedAt": datetime.utcnow(),
                "gradingDetails": details,
            }
        },
    )

    # --- TRIGGER ADAPTIVE PIPELINE ---
    # Only trigger for AI-generated quizzes — teacher quizzes don't need
    # the classify → generate-next-lesson flow.  The old `or True` was a
    # debug leftover that doubled Gemini quota usage on every submission.
    if is_ai_quiz:
        import asyncio
        from app.services.lesson_generator import generate_lesson_for_student
        
        async def trigger_next_lesson():
            try:
                # Find the next topic in the course sequence
                course = await db.courses.find_one({"_id": data["courseId"]})
                next_topic = None
                next_lesson_id = None
                if course:
                    found_curr = False
                    current_lesson_id = quiz.get("lessonId")
                    quiz_topic = quiz.get("topic")
                    for m in course.get("modules", []):
                        for l in m.get("lessons", []):
                            if found_curr and l.get("type") != "quiz":
                                next_topic = l.get("title")
                                next_lesson_id = str(l.get("id")) if l.get("id") else None
                                break
                            if current_lesson_id and str(l.get("id")) == str(current_lesson_id):
                                found_curr = True
                            elif not current_lesson_id and l.get("title") == quiz_topic:
                                found_curr = True
                        if next_topic: break
                
                if next_topic:
                    await generate_lesson_for_student(
                        student_id=student_id,
                        course_id=str(data["courseId"]),
                        topic=next_topic,
                        quiz_id=str(data["quizId"]),
                        lesson_id=next_lesson_id,
                        score_percentage=pct,
                        tenant_id=str(data["tenantId"]) if data.get("tenantId") else tenant_id
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Quiz Trigger Error: {e}")

        asyncio.create_task(trigger_next_lesson())

    return serialize_submission(
        await db.quizSubmissions.find_one({"_id": res.inserted_id})
    )


async def get_quiz_summary(quiz_id: str, top_n: int = 5):
    from app.crud._quiz_analytics import get_quiz_summary as _impl

    return await _impl(quiz_id, top_n)


async def get_student_analytics(student_id: str, recent: int = 5):
    from app.crud._quiz_analytics import get_student_analytics as _impl

    return await _impl(student_id, recent)


async def get_teacher_dashboard(teacher_id: str, course_id: Optional[str] = None):
    from app.crud._quiz_analytics import get_teacher_dashboard as _impl

    return await _impl(teacher_id, course_id)


async def get_by_quiz(quiz_id, sort=None, tenant_id: str | None = None):
    query = {"quizId": ObjectId(quiz_id)}
    if tenant_id:
        query["tenantId"] = ObjectId(tenant_id)
    cursor = db.quizSubmissions.find(query)
    if sort:
        cursor = cursor.sort(sort)
    return [serialize_submission(s) async for s in cursor]


async def get_by_student(student_id, sort=None, tenant_id: str | None = None):
    query = {"studentId": ObjectId(student_id)}
    if tenant_id:
        query["tenantId"] = ObjectId(tenant_id)
    cursor = db.quizSubmissions.find(query)
    if sort:
        cursor = cursor.sort(sort)
    return [serialize_submission(s) async for s in cursor]


async def get_submission_by_id(_id: str):
    return await db.quizSubmissions.find_one({"_id": ObjectId(_id)})


async def delete_submission(_id: str, tenant_id: str | None = None):
    query = {"_id": ObjectId(_id)}
    if tenant_id:
        query["tenantId"] = ObjectId(tenant_id)
    return (await db.quizSubmissions.delete_one(query)).deleted_count > 0
