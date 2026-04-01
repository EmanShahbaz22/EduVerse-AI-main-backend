"""
lesson_generator.py — The "Lesson Factory" 🏭

WHY THIS FILE EXISTS:
    This is the GLUE between the classifier and the AI service.
    It connects everything like a pipeline:

    Quiz Result → Classifier → AI Service → Saved Lesson

FIXES APPLIED:
    1. Duplicate check now logs a QUOTA WARNING when force=True is used,
       making accidental force usage visible immediately in logs.
    2. Error log in Step 2 now includes the actual exception message,
       so you know WHY the AI call failed (quota, bad key, parse error, etc.)
    3. classification_doc variable renamed to classification_db_doc in Step 3
       to avoid shadowing the classification_doc fetched in the duplicate path.
    4. Defensive .get() added when reading classificationId from existing_lesson
       so a corrupted DB doc doesn't cause an unhandled KeyError crash.
"""

import logging
from datetime import datetime, timezone
from bson import ObjectId
from typing import Optional

from app.services.student_classifier import classify_student
from app.services.ai_service import generate_lesson, generate_base_lesson
from app.db.database import db
from bson.errors import InvalidId

logger = logging.getLogger(__name__)


def safe_object_id(value: str) -> Optional[ObjectId]:
    """Gracefully attempt to convert a string to an ObjectId."""
    try:
        if not value: return None
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


async def generate_lesson_for_student(
    student_id: str,
    course_id: str,
    topic: str,
    quiz_id: Optional[str] = None,
    lesson_id: Optional[str] = None,
    score_percentage: float = 100.0,
    weak_areas: Optional[str] = None,
    time_spent_seconds: Optional[float] = None,
    time_limit_seconds: Optional[float] = None,
    tenant_id: Optional[str] = None,
    force: bool = False,
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
        force: If True, skips duplicate check and always generates a new lesson.
               WARNING: This burns Gemini API quota. Only use during development.

    Returns:
        dict with: classification result + generated lesson + database IDs
    """

    # ── Step 0: Validation ──
    try:
        s_id = safe_object_id(student_id)
        if not s_id: raise ValueError("student_id must be a valid ObjectId")
        
        # Course and Quiz IDs can be custom strings or ObjectIds
        c_oid = safe_object_id(course_id)
        q_oid = safe_object_id(quiz_id) if quiz_id else None
        
        course_query = {"$or": [{"_id": c_oid}, {"_id": course_id}]} if c_oid else {"_id": course_id}
        quiz_query = None
        if quiz_id:
            quiz_query = {"$or": [{"_id": q_oid}, {"_id": quiz_id}]} if q_oid else {"_id": quiz_id}
            
    except Exception as e:
        raise ValueError(f"Invalid ID format: {str(e)}")

    # ── Step 0b: Duplicate Check ──
    # FIX: Warn loudly when force=True — this bypass costs real API quota.
    # In normal usage (production, regular testing), force should always be False.
    if force:
        logger.warning(
            "QUOTA WARNING: force=True — duplicate guard SKIPPED for "
            "student=%s quiz=%s. A new Gemini API call WILL be made. "
            "Set force=False unless you intentionally want to regenerate.",
            student_id, quiz_id,
        )

    existing_lesson = None
    if not force:
        duplicate_query = {
            "studentId": {"$in": [s_id, student_id]},
            "courseId": {"$in": [c_oid, course_id]} if c_oid else course_id,
        }
        if lesson_id:
            duplicate_query["lessonId"] = lesson_id
        elif quiz_id:
            duplicate_query["quizId"] = {"$in": [q_oid, quiz_id]} if q_oid else quiz_id

        existing_lesson = await db.aiGeneratedLessons.find_one(duplicate_query)

    if existing_lesson:
        logger.info(
            "Duplicate found: lesson already exists for student=%s quiz=%s. "
            "Returning cached lesson (no Gemini API call made).",
            student_id, quiz_id,
        )

        # FIX: Defensive .get() for classificationId — avoids KeyError if
        # the DB doc is missing this field due to a previous partial write.
        classification_id = existing_lesson.get("classificationId")
        classification_doc = None
        if classification_id:
            classification_doc = await db.studentClassifications.find_one(
                {"_id": classification_id}
            )

        return {
            "classification": {
                "id": str(classification_doc["_id"]) if classification_doc else None,
                "pace": existing_lesson.get("pace", ""),
                "score": classification_doc.get("score") if classification_doc else score_percentage,
                "factors": classification_doc.get("factors") if classification_doc else [],
            },
            "lesson": {
                "id": str(existing_lesson["_id"]),
                "title": existing_lesson.get("title"),
                "content": existing_lesson.get("content"),
                "lessonId": existing_lesson.get("lessonId"),
                "sourceTopic": existing_lesson.get("sourceTopic"),
                "generationType": existing_lesson.get("generationType", "adaptive"),
                "difficulty": existing_lesson.get("difficulty"),
                "estimatedDurationMinutes": existing_lesson.get("estimatedDurationMinutes"),
                "keyConcepts": existing_lesson.get("keyConcepts"),
                "summary": existing_lesson.get("summary"),
            },
            "studentId": student_id,
            "courseId": course_id,
            "quizId": quiz_id,
            "isDuplicate": True,
        }

    # ── Step 1: Classify the student ──
    logger.info(
        "Step 1: Classifying student=%s score=%.1f%%",
        student_id, score_percentage,
    )
    classification = classify_student(
        score_percentage=score_percentage,
        time_spent_seconds=time_spent_seconds,
        time_limit_seconds=time_limit_seconds,
    )
    logger.info("Step 1 complete: pace=%s", classification["pace"])

    # ── Step 2: Generate AI lesson ──
    # Done BEFORE saving to DB — if the AI call fails, no orphan records are left.
    logger.info(
        "Step 2: Calling Gemini for pace='%s' topic='%s'",
        classification["pace"], topic,
    )

    if not weak_areas:
        weak_areas = "General review needed"

    try:
        lesson_data = await generate_lesson(
            pace=classification["pace"],
            topic=topic,
            score=score_percentage,
            weak_areas=weak_areas,
        )
    except Exception as e:
        # FIX: Log the actual error message so you can diagnose quota vs key vs parse issues.
        # Old code just said "AI lesson generation failed" with no detail.
        logger.error(
            "Step 2 FAILED — Gemini call unsuccessful. No DB changes made. "
            "Reason: %s", str(e),
        )
        raise

    logger.info("Step 2 complete: lesson title='%s'", lesson_data.get("title", "Untitled"))

    # ── Step 3: Save classification to database ──
    # If no quiz_id, we still save the classification but it's linked only to the course
    classification_db_doc = {
        "studentId": s_id,
        "courseId": c_oid if c_oid else course_id,
        "quizId": q_oid if q_oid else quiz_id, # Can be None
        "lessonId": lesson_id,
        "tenantId": ObjectId(tenant_id) if safe_object_id(tenant_id) else None,
        "pace": classification["pace"],
        "score": classification["score"],
        "factors": classification["factors"],
        "classifiedAt": datetime.now(timezone.utc),
    }
    classification_result = await db.studentClassifications.insert_one(classification_db_doc)
    logger.info("Step 3 complete: classification saved id=%s", classification_result.inserted_id)

    # ── Step 4: Save generated lesson to database ──
    lesson_db_doc = {
        "studentId": s_id,
        "courseId": c_oid if c_oid else course_id,
        "quizId": q_oid if q_oid else quiz_id,
        "lessonId": lesson_id,
        "sourceTopic": topic,
        "tenantId": ObjectId(tenant_id) if safe_object_id(tenant_id) else None,
        "classificationId": classification_result.inserted_id,
        "generationType": "adaptive",
        "pace": classification["pace"],
        "title": lesson_data.get("title", "AI Generated Lesson"),
        "content": lesson_data.get("content", ""),
        "difficulty": lesson_data.get("difficulty", "intermediate"),
        "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes", 10),
        "keyConcepts": lesson_data.get("key_concepts", []),
        "summary": lesson_data.get("summary", ""),
        "generatedAt": datetime.now(timezone.utc),
    }
    lesson_result = await db.aiGeneratedLessons.insert_one(lesson_db_doc)
    logger.info("Step 4 complete: lesson saved id=%s", lesson_result.inserted_id)

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
            "lessonId": lesson_id,
            "sourceTopic": topic,
            "generationType": "adaptive",
            "difficulty": lesson_data.get("difficulty"),
            "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes"),
            "keyConcepts": lesson_data.get("key_concepts"),
            "summary": lesson_data.get("summary"),
        },
        "studentId": student_id,
        "courseId": course_id,
        "quizId": quiz_id,
        "isDuplicate": False,
    }


async def generate_base_lesson_for_student(
    student_id: str,
    course_id: str,
    lesson_id: str,
    topic: str,
    source_content: str,
    tenant_id: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Generate the initial/base lesson from teacher-authored content only.

    This path intentionally skips classification so Lesson 1 can be AI-expanded
    without affecting the quiz-driven adaptive flow used for Lesson 2+.
    """
    try:
        s_id = safe_object_id(student_id)
        if not s_id:
            raise ValueError("student_id must be a valid ObjectId")

        c_oid = safe_object_id(course_id)
        if not lesson_id or not str(lesson_id).strip():
            raise ValueError("lesson_id must not be empty")
    except Exception as e:
        raise ValueError(f"Invalid ID format: {str(e)}")

    clean_source_content = (source_content or "").strip()
    if not clean_source_content:
        raise ValueError("source_content must not be empty")

    if force:
        logger.warning(
            "QUOTA WARNING: force=True - base lesson duplicate guard skipped for "
            "student=%s lesson=%s. A new Gemini API call WILL be made.",
            student_id, lesson_id,
        )

    existing_lesson = None
    if not force:
        duplicate_query = {
            "studentId": {"$in": [s_id, student_id]},
            "courseId": {"$in": [c_oid, course_id]} if c_oid else course_id,
            "lessonId": lesson_id,
            "generationType": "base",
        }
        existing_lesson = await db.aiGeneratedLessons.find_one(duplicate_query)

    if existing_lesson:
        logger.info(
            "Base lesson duplicate found for student=%s lesson=%s. Returning cached lesson.",
            student_id, lesson_id,
        )
        return {
            "lesson": {
                "id": str(existing_lesson["_id"]),
                "title": existing_lesson.get("title"),
                "content": existing_lesson.get("content"),
                "lessonId": existing_lesson.get("lessonId"),
                "sourceTopic": existing_lesson.get("sourceTopic"),
                "generationType": existing_lesson.get("generationType", "base"),
                "difficulty": existing_lesson.get("difficulty"),
                "estimatedDurationMinutes": existing_lesson.get("estimatedDurationMinutes"),
                "keyConcepts": existing_lesson.get("keyConcepts"),
                "summary": existing_lesson.get("summary"),
            },
            "studentId": student_id,
            "courseId": course_id,
            "quizId": None,
            "isDuplicate": True,
        }

    logger.info(
        "Generating base lesson for student=%s lesson=%s topic='%s'",
        student_id, lesson_id, topic,
    )
    lesson_data = await generate_base_lesson(
        topic=topic,
        source_content=clean_source_content[:5000],
    )

    lesson_db_doc = {
        "studentId": s_id,
        "courseId": c_oid if c_oid else course_id,
        "quizId": None,
        "lessonId": lesson_id,
        "sourceTopic": topic,
        "generationType": "base",
        "tenantId": ObjectId(tenant_id) if safe_object_id(tenant_id) else None,
        "title": lesson_data.get("title", "AI Generated Lesson"),
        "content": lesson_data.get("content", ""),
        "difficulty": lesson_data.get("difficulty", "intermediate"),
        "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes", 10),
        "keyConcepts": lesson_data.get("key_concepts", []),
        "summary": lesson_data.get("summary", ""),
        "generatedAt": datetime.now(timezone.utc),
    }
    lesson_result = await db.aiGeneratedLessons.insert_one(lesson_db_doc)

    return {
        "lesson": {
            "id": str(lesson_result.inserted_id),
            "title": lesson_data.get("title"),
            "content": lesson_data.get("content"),
            "lessonId": lesson_id,
            "sourceTopic": topic,
            "generationType": "base",
            "difficulty": lesson_data.get("difficulty"),
            "estimatedDurationMinutes": lesson_data.get("estimated_duration_minutes"),
            "keyConcepts": lesson_data.get("key_concepts"),
            "summary": lesson_data.get("summary"),
        },
        "studentId": student_id,
        "courseId": course_id,
        "quizId": None,
        "isDuplicate": False,
    }


async def get_student_lessons(student_id: str, course_id: Optional[str] = None) -> list:
    """
    Get all AI-generated lessons for a student.
    Optionally filter by course.
    """
    s_oid = safe_object_id(student_id)
    query = {"studentId": {"$in": [s_oid, student_id]}} if s_oid else {"studentId": student_id}
    
    if course_id:
        c_oid = safe_object_id(course_id)
        query["courseId"] = {"$in": [c_oid, course_id]} if c_oid else course_id

    lessons = []
    async for doc in db.aiGeneratedLessons.find(query).sort("generatedAt", -1):
        lessons.append({
            "id": str(doc["_id"]),
            "title": doc.get("title", ""),
            "content": doc.get("content", ""),
            "lessonId": doc.get("lessonId"),
            "sourceTopic": doc.get("sourceTopic"),
            "generationType": doc.get("generationType"),
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
    s_oid = safe_object_id(student_id)
    query = {"studentId": {"$in": [s_oid, student_id]}} if s_oid else {"studentId": student_id}
    
    if course_id:
        c_oid = safe_object_id(course_id)
        query["courseId"] = {"$in": [c_oid, course_id]} if c_oid else course_id

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
