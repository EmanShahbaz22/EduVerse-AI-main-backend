from __future__ import annotations
"""
Adaptive Learning Router — The API Endpoints 🌐

WHY THIS FILE EXISTS:
    This is where we define the URLs that the frontend will call.
    Each endpoint is like a "button" the frontend can press:

    - POST /adaptive/generate-lesson  →  "Generate an AI lesson for me"
    - POST /adaptive/classify/{id}    →  "Tell me if this student is slow/avg/fast"
    - GET  /adaptive/student/{id}/...  →  "Show me the data"

    FastAPI automatically creates Swagger docs at /docs so you can test these!
"""

import logging
from fastapi import APIRouter, HTTPException
from app.schemas.adaptive_learning import (
    LessonGenerationRequest,
    ClassifyStudentRequest,
    ClassificationResponse,
    GeneratedLessonResponse,
)
from app.services.student_classifier import classify_student, get_pace_description
from app.services.lesson_generator import (
    generate_lesson_for_student,
    get_student_lessons,
    get_latest_classification,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/adaptive",
    tags=["Adaptive Learning (AI)"],
)


# ──────────────────────────────────────────────
# POST /adaptive/classify/{student_id}
# ──────────────────────────────────────────────
# "Hey system, classify this student based on their quiz score"

@router.post(
    "/classify/{student_id}",
    summary="Classify student learning pace",
    description="Takes a quiz score and classifies the student as slow, average, or fast.",
)
async def classify_student_endpoint(student_id: str, request: ClassifyStudentRequest):
    """
    Classify a student's learning pace based on quiz performance.

    This is just the classification step — it doesn't generate a lesson.
    Use /adaptive/generate-lesson for the full pipeline.
    """
    try:
        result = classify_student(
            score_percentage=request.scorePercentage,
            time_spent_seconds=request.timeSpentSeconds,
            time_limit_seconds=request.timeLimitSeconds,
        )
        result["description"] = get_pace_description(result["pace"])
        return result
    except Exception as e:
        logger.error("Classification failed for student %s: %s", student_id, str(e))
        raise HTTPException(status_code=500, detail=f"Classification failed: {str(e)}")


# ──────────────────────────────────────────────
# POST /adaptive/generate-lesson
# ──────────────────────────────────────────────
# "Hey AI, generate a personalized lesson for this student!"
# This is the BIG ONE — the full pipeline.

@router.post(
    "/generate-lesson",
    summary="Generate AI-powered personalized lesson",
    description="Classifies the student, then uses LangChain + Gemini to generate a personalized lesson.",
)
async def generate_lesson_endpoint(
    student_id: str,
    request: LessonGenerationRequest,
    tenant_id: str | None = None,
):
    """
    Full pipeline: classify student → generate AI lesson → save to database.

    Requires a valid GEMINI_API_KEY in .env to work.
    """
    try:
        # We need the student's quiz score. Let's get it from the quiz submission.
        from app.db.database import db
        from bson import ObjectId

        # Look up the quiz submission to get the score
        submission = await db.quizSubmissions.find_one({
            "studentId": ObjectId(student_id),
            "quizId": ObjectId(request.quizId),
        })

        if not submission:
            raise HTTPException(
                status_code=404,
                detail="No quiz submission found for this student and quiz."
            )

        score = submission.get("percentage", 0)

        # Determine weak areas from grading details
        weak_areas = request.weakAreas
        if not weak_areas and submission.get("gradingDetails"):
            # Extract questions the student got wrong
            wrong_questions = [
                f"Question {d['questionIndex'] + 1}"
                for d in submission["gradingDetails"]
                if not d.get("isCorrect", False)
            ]
            if wrong_questions:
                weak_areas = ", ".join(wrong_questions)

        # Run the full pipeline
        result = await generate_lesson_for_student(
            student_id=student_id,
            course_id=request.courseId,
            quiz_id=request.quizId,
            score_percentage=score,
            topic=request.topic,
            weak_areas=weak_areas,
            tenant_id=tenant_id,
        )

        return result

    except HTTPException:
        raise
    except ValueError as e:
        # This catches the "GEMINI_API_KEY not set" error
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("Lesson generation failed: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Lesson generation failed: {str(e)}"
        )


# ──────────────────────────────────────────────
# GET /adaptive/student/{student_id}/classification
# ──────────────────────────────────────────────
# "What's this student's current learning pace?"

@router.get(
    "/student/{student_id}/classification",
    summary="Get student's latest classification",
    description="Returns the most recent slow/average/fast classification for a student.",
)
async def get_classification_endpoint(student_id: str, course_id: str | None = None):
    """Get the student's most recent classification."""
    try:
        result = await get_latest_classification(student_id, course_id)
        if not result:
            raise HTTPException(
                status_code=404,
                detail="No classification found for this student."
            )
        result["description"] = get_pace_description(result["pace"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get classification: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# GET /adaptive/student/{student_id}/generated-lessons
# ──────────────────────────────────────────────
# "Show me all the AI lessons this student has received"

@router.get(
    "/student/{student_id}/generated-lessons",
    summary="Get all AI-generated lessons for a student",
    description="Returns a list of all AI-generated lessons, newest first.",
)
async def get_generated_lessons_endpoint(student_id: str, course_id: str | None = None):
    """Get all AI-generated lessons for a student."""
    try:
        lessons = await get_student_lessons(student_id, course_id)
        return {"lessons": lessons, "count": len(lessons)}
    except Exception as e:
        logger.error("Failed to get lessons: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))
