"""
lesson_generator.py — The "Lesson Factory" 🏭

WHY THIS FILE EXISTS:
    This is the GLUE between the classifier and the AI service.
    It connects everything like a pipeline:

    Quiz Result → Classifier → AI Service → Saved Lesson

    Think of it like an assembly line:
    1. A quiz score comes in
    2. We classify the student (slow/average/fast)
    3. We ask AI to generate a lesson for that level
    4. We save the lesson to the database
    5. We return the lesson so the student can read it

WHAT IT DOES:
    - Takes quiz submission data
    - Calls student_classifier.py to get the pace
    - Calls ai_service.py to generate a lesson
    - Saves both classification + lesson to MongoDB
    - Returns everything nicely packaged
"""

import logging
from datetime import datetime, timezone
from bson import ObjectId
from typing import Optional

from app.services.student_classifier import classify_student
from app.services.ai_service import generate_lesson
from app.db.database import db

logger = logging.getLogger(__name__)


async def generate_lesson_for_student(
    student_id: str,
    course_id: str,
    quiz_id: str,
    score_percentage: float,
    topic: str,
    weak_areas: Optional[str] = None,
    time_spent_seconds: Optional[float] = None,
    time_limit_seconds: Optional[float] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    The FULL PIPELINE: classify student → generate AI lesson → save to DB.

    Args:
        student_id: The student's MongoDB ID
        course_id: The course this quiz belongs to
        quiz_id: The quiz that was just submitted
        score_percentage: The quiz score (0-100)
        topic: What the quiz/course is about (e.g., "Algebra - Quadratic Equations")
        weak_areas: Comma-separated list of topics the student got wrong
        time_spent_seconds: How long the student took (optional)
        time_limit_seconds: The quiz time limit (optional)
        tenant_id: The organization/tenant ID

    Returns:
        dict with: classification result + generated lesson + database IDs
    """

    # ── Step 1: Classify the student ──
    logger.info("Step 1: Classifying student %s (score: %.1f%%)", student_id, score_percentage)
    classification = classify_student(
        score_percentage=score_percentage,
        time_spent_seconds=time_spent_seconds,
        time_limit_seconds=time_limit_seconds,
    )

    # ── Step 2: Save classification to database ──
    classification_doc = {
        "studentId": ObjectId(student_id),
        "courseId": ObjectId(course_id),
        "quizId": ObjectId(quiz_id),
        "tenantId": ObjectId(tenant_id) if tenant_id else None,
        "pace": classification["pace"],
        "score": classification["score"],
        "factors": classification["factors"],
        "classifiedAt": datetime.now(timezone.utc),
    }
    classification_result = await db.studentClassifications.insert_one(classification_doc)
    logger.info("Step 2: Classification saved (ID: %s)", classification_result.inserted_id)

    # ── Step 3: Generate AI lesson ──
    logger.info("Step 3: Generating AI lesson for pace='%s', topic='%s'", classification["pace"], topic)

    if not weak_areas:
        weak_areas = "General review needed"

    lesson_data = await generate_lesson(
        pace=classification["pace"],
        topic=topic,
        score=score_percentage,
        weak_areas=weak_areas,
    )

    # ── Step 4: Save generated lesson to database ──
    lesson_doc = {
        "studentId": ObjectId(student_id),
        "courseId": ObjectId(course_id),
        "quizId": ObjectId(quiz_id),
        "tenantId": ObjectId(tenant_id) if tenant_id else None,
        "classificationId": classification_result.inserted_id,
        "pace": classification["pace"],
        "title": lesson_data.get("title", "AI Generated Lesson"),
        "content": lesson_data.get("content", ""),
        "difficulty": lesson_data.get("difficulty", "intermediate"),
        "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes", 10),
        "keyConcepts": lesson_data.get("key_concepts", []),
        "summary": lesson_data.get("summary", ""),
        "generatedAt": datetime.now(timezone.utc),
    }
    lesson_result = await db.aiGeneratedLessons.insert_one(lesson_doc)
    logger.info("Step 4: Lesson saved (ID: %s)", lesson_result.inserted_id)

    # ── Step 5: Return everything ──
    return {
        "classification": {
            "id": str(classification_result.inserted_id),
            "pace": classification["pace"],
            "score": classification["score"],
            "factors": classification["factors"],
        },
        "lesson": {
            "id": str(lesson_result.inserted_id),
            "title": lesson_data.get("title"),
            "content": lesson_data.get("content"),
            "difficulty": lesson_data.get("difficulty"),
            "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes"),
            "keyConcepts": lesson_data.get("key_concepts"),
            "summary": lesson_data.get("summary"),
        },
        "studentId": student_id,
        "courseId": course_id,
        "quizId": quiz_id,
    }


async def get_student_lessons(student_id: str, course_id: Optional[str] = None) -> list:
    """
    Get all AI-generated lessons for a student.
    Optionally filter by course.
    """
    query = {"studentId": ObjectId(student_id)}
    if course_id:
        query["courseId"] = ObjectId(course_id)

    lessons = []
    async for doc in db.aiGeneratedLessons.find(query).sort("generatedAt", -1):
        lessons.append({
            "id": str(doc["_id"]),
            "title": doc.get("title", ""),
            "content": doc.get("content", ""),
            "difficulty": doc.get("difficulty", ""),
            "pace": doc.get("pace", ""),
            "estimatedDurationMinutes": doc.get("estimatedDurationMinutes", 0),
            "keyConcepts": doc.get("keyConcepts", []),
            "summary": doc.get("summary", ""),
            "courseId": str(doc.get("courseId", "")),
            "quizId": str(doc.get("quizId", "")),
            "generatedAt": doc.get("generatedAt", ""),
        })
    return lessons


async def get_latest_classification(student_id: str, course_id: Optional[str] = None) -> Optional[dict]:
    """
    Get the most recent classification for a student.
    """
    query = {"studentId": ObjectId(student_id)}
    if course_id:
        query["courseId"] = ObjectId(course_id)

    doc = await db.studentClassifications.find_one(
        query,
        sort=[("classifiedAt", -1)]
    )

    if not doc:
        return None

    return {
        "id": str(doc["_id"]),
        "pace": doc.get("pace", ""),
        "score": doc.get("score", 0),
        "factors": doc.get("factors", []),
        "courseId": str(doc.get("courseId", "")),
        "quizId": str(doc.get("quizId", "")),
        "classifiedAt": doc.get("classifiedAt", ""),
    }