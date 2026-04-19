from __future__ import annotations
"""
Adaptive Learning Router — The API Endpoints 🌐

Endpoints:
    POST /adaptive/generate-lesson       → full lesson object (content = Markdown)
    POST /adaptive/classify/{id}         → pace classification
    GET  /adaptive/student/{id}/generated-lessons  → array of lesson objects
    GET  /adaptive/student/{id}/classification     → {score, pace, weakAreas}
    POST /adaptive/generate-quiz         → quiz object with questions array
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from app.schemas.adaptive_learning import (
    LessonGenerationRequest,
    BaseLessonGenerationRequest,
    ClassifyStudentRequest,
    AIQuizRequest,
)
from app.services.student_classifier import classify_student, get_pace_description
from app.services.lesson_generator import (
    generate_lesson_for_student,
    generate_base_lesson_for_student,
    get_student_lessons,
    get_latest_classification,
    safe_object_id,
)
from app.services.ai_service import RateLimitError
from app.services.quiz_generator import generate_ai_quiz
from app.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/adaptive",
    tags=["Adaptive Learning (AI)"],
)

# ── Rate-limit helper ─────────────────────────────────────────────────────────

def _rate_limit_response(context: str) -> JSONResponse:
    """Return a clean HTTP 503 whenever Gemini quota is hit."""
    logger.warning("Gemini rate limit hit in: %s", context)
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": "60"},
        content={
            "detail": (
                "The AI service is temporarily rate-limited (Gemini quota exceeded). "
                "Please retry in ~60 seconds."
            ),
            "retryAfterSeconds": 60,
        },
    )


# ──────────────────────────────────────────────
# POST /adaptive/classify/{student_id}
# ──────────────────────────────────────────────

@router.post(
    "/classify/{student_id}",
    summary="Classify student learning pace",
    description="Takes a quiz score and classifies the student as slow, average, or fast.",
)
async def classify_student_endpoint(
    student_id: str,
    request: ClassifyStudentRequest,
    current_user: dict = Depends(get_current_user),
):
    """Classify a student's learning pace based on quiz performance."""
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
# Full pipeline: classify → generate → save.
# tenant_id is pulled from the authenticated user context, never from query params.
#
# Response shape (frontend contract):
#   { id, title, content (Markdown), difficulty, estimatedDurationMinutes,
#     keyConcepts, summary, courseId, quizId, ... }

@router.post(
    "/generate-lesson",
    summary="Generate AI-powered personalized lesson",
    description="Classifies the student, then uses Gemini to generate a personalized lesson.",
)
async def generate_lesson_endpoint(
    student_id: str,
    request: LessonGenerationRequest,
    force: bool = Query(False, description="Skip cached lesson and regenerate. Dev only."),
    current_user: dict = Depends(get_current_user),
):
    """
    Full pipeline: classify student → generate AI lesson → save to database.
    Returns the lesson object directly with content as a Markdown string.
    """
    # tenant_id comes from the authenticated user — never from a raw query param.
    tenant_id: str | None = current_user.get("tenant_id")

    try:
        from app.db.database import db
        
        s_oid = safe_object_id(student_id)
        if not s_oid:
            raise HTTPException(status_code=400, detail="Invalid student_id format")

        score = 100.0  # Default for initial lessons
        time_spent = None
        time_limit = None
        weak_areas = request.weakAreas

        if request.quizId:
            q_oid = safe_object_id(request.quizId)
            quiz_query = {"$or": [{"_id": q_oid}, {"_id": request.quizId}]} if q_oid else {"_id": request.quizId}
            
            # Look up the quiz submission to get the score
            submission = await db.quizSubmissions.find_one({
                "studentId": {"$in": [s_oid, student_id]},
                "quizId": {"$in": [q_oid, request.quizId]} if q_oid else request.quizId,
            })

            if not submission:
                logger.info(f"No quiz submission found for quiz {request.quizId}, using provided score {request.scorePercentage}")
                score = request.scorePercentage if request.scorePercentage is not None else 100.0
            else:
                if submission.get("status") != "graded":
                    raise HTTPException(
                        status_code=400,
                        detail="Quiz submission is not graded yet.",
                    )

                score = submission.get("percentage", 100)
                time_spent = submission.get("timeSpentSeconds")
                time_limit = submission.get("timeLimitSeconds")

                # Derive weak areas from grading details if not provided
                if not weak_areas and submission.get("gradingDetails"):
                    from app.db.database import ai_quiz_sessions_collection
                    quiz = await db.quizzes.find_one(quiz_query)
                    if not quiz:
                        quiz = await ai_quiz_sessions_collection.find_one(quiz_query)
                    
                    if quiz and quiz.get("questions"):
                        questions_list = quiz["questions"]
                        wrong_points = []
                        for detail in submission["gradingDetails"]:
                            if not detail.get("isCorrect", False):
                                idx = detail["questionIndex"]
                                if 0 <= idx < len(questions_list):
                                    q_text = questions_list[idx].get("question", "Question")
                                    wrong_points.append(f"Conceptual gap in: '{q_text}'")
                        if wrong_points:
                            weak_areas = "; ".join(wrong_points[:5])

        if not weak_areas:
            weak_areas = "General review of the topic"

        # Run the full pipeline
        result = await generate_lesson_for_student(
            student_id=student_id,
            course_id=request.courseId,
            quiz_id=request.quizId,
            score_percentage=score,
            topic=request.topic,
            weak_areas=weak_areas,
            time_spent_seconds=time_spent,
            time_limit_seconds=time_limit,
            tenant_id=tenant_id,
            force=force,
        )

        # ── Flatten to the shape the Angular frontend expects ──
        # The frontend reads the lesson fields directly from the response root.
        lesson = result.get("lesson", {})
        return {
            "id": lesson.get("id"),
            "title": lesson.get("title"),
            "content": lesson.get("content", ""),   # must be Markdown string
            "lessonId": lesson.get("lessonId"),
            "sourceTopic": lesson.get("sourceTopic"),
            "generationType": lesson.get("generationType"),
            "difficulty": lesson.get("difficulty"),
            "estimatedDurationMinutes": lesson.get("estimatedDurationMinutes"),
            "keyConcepts": lesson.get("keyConcepts", []),
            "summary": lesson.get("summary", ""),
            "courseId": result.get("courseId"),
            "quizId": result.get("quizId"),
            "studentId": result.get("studentId"),
            "isDuplicate": result.get("isDuplicate", False),
            "pace": result.get("classification", {}).get("pace"),
        }

    except HTTPException:
        raise
    except RateLimitError:
        return _rate_limit_response("generate-lesson")
    except ValueError as e:
        err_msg = str(e)
        if "API_KEY" in err_msg or "not set" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        logger.error("Configuration or parse error in generate-lesson: %s", err_msg)
        raise HTTPException(status_code=500, detail=f"Generation failed: {err_msg}")
    except Exception as e:
        logger.error("Lesson generation failed: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Lesson generation failed: {str(e)}")


@router.post(
    "/generate-base-lesson",
    summary="Generate AI lesson from teacher-authored base lesson content",
    description="Expands the teacher's lesson description into a richer student-facing lesson without classifying pace.",
)
async def generate_base_lesson_endpoint(
    student_id: str,
    request: BaseLessonGenerationRequest,
    force: bool = Query(False, description="Skip cached lesson and regenerate. Dev only."),
    current_user: dict = Depends(get_current_user),
):
    tenant_id: str | None = current_user.get("tenant_id")

    try:
        s_oid = safe_object_id(student_id)
        if not s_oid:
            raise HTTPException(status_code=400, detail="Invalid student_id format")

        result = await generate_base_lesson_for_student(
            student_id=student_id,
            course_id=request.courseId,
            lesson_id=request.lessonId,
            topic=request.topic,
            source_content=request.sourceContent,
            tenant_id=tenant_id,
            force=force,
        )

        lesson = result.get("lesson", {})
        return {
            "id": lesson.get("id"),
            "title": lesson.get("title"),
            "content": lesson.get("content", ""),
            "lessonId": lesson.get("lessonId"),
            "sourceTopic": lesson.get("sourceTopic"),
            "generationType": lesson.get("generationType"),
            "difficulty": lesson.get("difficulty"),
            "estimatedDurationMinutes": lesson.get("estimatedDurationMinutes"),
            "keyConcepts": lesson.get("keyConcepts", []),
            "summary": lesson.get("summary", ""),
            "courseId": result.get("courseId"),
            "quizId": result.get("quizId"),
            "studentId": result.get("studentId"),
            "isDuplicate": result.get("isDuplicate", False),
            "pace": None,
        }
    except HTTPException:
        raise
    except RateLimitError:
        return _rate_limit_response("generate-base-lesson")
    except ValueError as e:
        err_msg = str(e)
        if "API_KEY" in err_msg or "not set" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        logger.error("Configuration or parse error in generate-base-lesson: %s", err_msg)
        raise HTTPException(status_code=500, detail=f"Generation failed: {err_msg}")
    except Exception as e:
        logger.error("Base lesson generation failed: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Base lesson generation failed: {str(e)}")


# ──────────────────────────────────────────────
# GET /adaptive/student/{student_id}/classification
# ──────────────────────────────────────────────
# Frontend expects: { score, pace, weakAreas }

@router.get(
    "/student/{student_id}/classification",
    summary="Get student's latest classification",
    description="Returns the most recent classification for a student.",
)
async def get_classification_endpoint(
    student_id: str,
    course_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the latest classification with exactly:
        { score, pace, weakAreas }
    (plus id, courseId, quizId, classifiedAt for internal use)
    """
    try:
        result = await get_latest_classification(student_id, course_id)
        if not result:
            raise HTTPException(
                status_code=404,
                detail="No classification found for this student.",
            )

        # Map stored "factors" list → weakAreas string expected by frontend.
        # factors is a list of strings describing what drove the pace result.
        factors = result.get("factors", [])
        weak_areas = ", ".join(factors) if factors else ""

        return {
            "id": result.get("id"),
            "score": result.get("score", 0),
            "pace": result.get("pace"),
            "weakAreas": weak_areas,
            "courseId": result.get("courseId"),
            "quizId": result.get("quizId"),
            "classifiedAt": result.get("classifiedAt"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get classification: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# GET /adaptive/student/{student_id}/generated-lessons
# ──────────────────────────────────────────────
# Frontend expects a plain array of lesson objects.

@router.get(
    "/student/{student_id}/generated-lessons",
    summary="Get all AI-generated lessons for a student",
    description="Returns a list of all AI-generated lessons, newest first.",
)
async def get_generated_lessons_endpoint(
    student_id: str,
    course_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """Returns a plain array of lesson objects for this student + course."""
    try:
        lessons = await get_student_lessons(student_id, course_id)
        # Frontend expects the array directly, not wrapped in an object.
        return lessons
    except Exception as e:
        logger.error("Failed to get lessons: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# POST /adaptive/generate-quiz
# ──────────────────────────────────────────────
# Returns: { id, courseId, topic, questions: [...] }

@router.post(
    "/generate-quiz",
    summary="Generate AI-powered MCQ quiz on a topic",
    description="Uses Gemini to generate a quiz based on a topic and difficulty.",
)
async def generate_quiz_endpoint(
    student_id: str,
    request: AIQuizRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generates an AI MCQ quiz and returns it with a questions array."""
    try:
        quiz = await generate_ai_quiz(
            student_id=student_id,
            course_id=request.courseId,
            topic=request.topic,
            difficulty=request.difficulty,
            count=request.count,
        )
        if "_id" in quiz:
            quiz["_id"] = str(quiz["_id"])
        return quiz
    except RateLimitError:
        return _rate_limit_response("generate-quiz")
    except ValueError as e:
        err_msg = str(e)
        if "API_KEY" in err_msg or "not set" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=500, detail=f"Quiz generation failed: {err_msg}")
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error("Quiz generation failed: %s\n%s", str(e), error_trace)
        raise HTTPException(status_code=500, detail=f"Quiz generation failed: {str(e)}")
