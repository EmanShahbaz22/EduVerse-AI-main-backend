"""
chat_tutor.py — AI Tutor Chat Service

MIGRATION: Replaced Gemini (_call_gemini_once) with local Ollama (ollama_service).

All context-building logic (lesson content, classification, history) is unchanged.
Only the LLM call (Step 6) now goes to the active local Ollama worker model
instead of the Gemini API.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from app.services.ollama_service import _call_ollama, get_active_model
from app.db.database import ai_chat_history_collection, db
from bson import ObjectId

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 2. Prompt template
# ──────────────────────────────────────────────

_PROMPT_PREFIX = """\
You are EduVerse AI, a friendly and professional educational tutor.
Your role is to explain concepts clearly, encourage the student, and answer their questions.

Guidelines:
- Use simple language for difficult terms.
- Break down concepts step by step when a student is struggling.
- Keep your tone encouraging and supportive.
- Provide concise, focused answers.\
- If current lesson context is provided, treat it as the source of truth.
- Never replace the lesson topic with quiz metadata or generic performance labels.
- When the student asks about the current lesson, answer directly from the lesson context first.\
"""

_PERFORMANCE_LINE = (
    "The student's latest relevant quiz score for this lesson was {score}% "
    "with a pace of {pace}."
)

_PERFORMANCE_WEAK_AREAS_LINE = "If helpful, reinforce these areas: {weak_areas}."

_LESSON_SECTION = "\nCurrent lesson context:\n{lesson_content}\n"

_PROMPT_SUFFIX = """
Current conversation:
{history}
Student: {input}
Tutor:"""


def _build_system_prompt(
    lesson_content: Optional[str],
    score: Optional[float],
    pace: Optional[str],
    weak_areas: Optional[str],
) -> str:
    parts = [_PROMPT_PREFIX]

    if lesson_content:
        parts.append(_LESSON_SECTION.format(lesson_content=lesson_content.strip()))

    if score is not None and pace is not None:
        parts.append("\n" + _PERFORMANCE_LINE.format(score=round(score, 1), pace=pace))
        if weak_areas:
            parts.append("\n" + _PERFORMANCE_WEAK_AREAS_LINE.format(weak_areas=weak_areas))

    return "".join(parts)


# ──────────────────────────────────────────────
# 3. Context helpers
# ──────────────────────────────────────────────

def _safe_object_id(value: Optional[str]) -> Optional[ObjectId]:
    try:
        if value and ObjectId.is_valid(value):
            return ObjectId(value)
    except Exception:
        return None
    return None


_GENERIC_CLASSIFICATION_FACTORS = {
    "low_score",
    "moderate_score",
    "high_score",
    "high_time_usage",
    "quick_completion",
    "downgraded_by_time",
    "upgraded_by_time",
}


def _format_weak_areas(factors: Optional[list]) -> Optional[str]:
    if not factors:
        return None
    meaningful_factors = [factor for factor in factors if factor not in _GENERIC_CLASSIFICATION_FACTORS]
    if not meaningful_factors:
        return None
    return ", ".join(meaningful_factors)


async def _fetch_lesson_content(course_id: str, lesson_id: Optional[str]) -> Optional[str]:
    if not lesson_id:
        return None
    try:
        c_oid = _safe_object_id(course_id)
        l_oid = _safe_object_id(lesson_id)

        course_match = {"$in": [c_oid, course_id]} if c_oid else course_id
        ai_query = {"courseId": course_match}
        ai_matchers = [{"lessonId": lesson_id}]
        if l_oid:
            ai_matchers.append({"_id": l_oid})

        ai_query["$or"] = ai_matchers
        ai_doc = await db.aiGeneratedLessons.find_one(ai_query, sort=[("generatedAt", -1)])
        if ai_doc:
            return ai_doc.get("content")

        course_query = {"$or": [{"_id": c_oid}, {"_id": course_id}]} if c_oid else {"_id": course_id}
        course_doc = await db.courses.find_one(course_query, {"modules": 1})
        if course_doc:
            for module in course_doc.get("modules", []):
                for lesson in module.get("lessons", []):
                    if str(lesson.get("id")) == lesson_id:
                        return lesson.get("content")
    except Exception as exc:
        logger.warning("Could not fetch lesson content (lesson=%s): %s", lesson_id, exc)
    return None


async def _fetch_classification(student_id: str, course_id: str, lesson_id: Optional[str] = None) -> Optional[dict]:
    try:
        s_oid = _safe_object_id(student_id)
        c_oid = _safe_object_id(course_id)

        base_query = {
            "studentId": {"$in": [s_oid, student_id]} if s_oid else student_id,
            "courseId": {"$in": [c_oid, course_id]} if c_oid else course_id,
        }
        query = dict(base_query)
        if lesson_id:
            query["lessonId"] = lesson_id

        doc = await db.studentClassifications.find_one(query, sort=[("classifiedAt", -1)])
        if not doc and not lesson_id:
            doc = await db.studentClassifications.find_one(base_query, sort=[("classifiedAt", -1)])

        if doc:
            return {
                "score": doc.get("score", 0),
                "pace": doc.get("pace", "average"),
                "factors": doc.get("factors", []),
            }
    except Exception as exc:
        logger.warning("Could not fetch classification (student=%s): %s", student_id, exc)
    return None


# ──────────────────────────────────────────────
# 4. RAG context helper (optional enrichment)
# ──────────────────────────────────────────────

async def _fetch_rag_context(course_id: str, lesson_id: Optional[str]) -> str:
    """
    Attempt to retrieve RAG context from ChromaDB for the lesson's reference upload.
    Returns empty string if no upload exists (graceful degradation).
    """
    if not lesson_id:
        return ""
    try:
        collection_id = f"{course_id}_{lesson_id}"
        from app.services.rag_service import retrieve_context
        return retrieve_context("tutor context", collection_id, top_k=3)
    except Exception as exc:
        logger.debug("RAG context retrieval skipped (lesson=%s): %s", lesson_id, exc)
        return ""


# ──────────────────────────────────────────────
# 5. Chat Tutor Service
# ──────────────────────────────────────────────

class ChatTutorService:
    @staticmethod
    async def get_chat_response(
        student_id: str,
        course_id: str,
        message: str,
        lesson_id: Optional[str] = None,
    ) -> Dict[str, Any]:

        logger.info(
            "AI Chat request from student %s for course %s lesson %s",
            student_id, course_id, lesson_id,
        )

        # ── Step 1: Fetch lesson content ──────────────────────────────────────
        lesson_content = await _fetch_lesson_content(course_id, lesson_id)
        if lesson_content:
            logger.debug("Lesson context injected (%d chars).", len(lesson_content))
        else:
            logger.debug("No lesson context available — continuing without it.")

        # ── Step 1b: Optional RAG context enrichment ──────────────────────────
        rag_context = await _fetch_rag_context(course_id, lesson_id)
        if rag_context:
            logger.debug("RAG context injected (%d chars).", len(rag_context))

        # ── Step 2: Fetch student classification ──────────────────────────────
        classification = await _fetch_classification(student_id, course_id, lesson_id)
        score: Optional[float] = None
        pace: Optional[str] = None
        weak_areas: Optional[str] = None

        if classification:
            score = classification["score"]
            pace = classification["pace"]
            weak_areas = _format_weak_areas(classification["factors"])
            logger.debug(
                "Classification injected: score=%.1f pace=%s weakAreas=%s",
                score, pace, weak_areas,
            )
        else:
            logger.debug("No classification found — first session, skipping performance line.")

        # ── Step 3: Build system prompt ───────────────────────────────────────
        system_prefix = _build_system_prompt(lesson_content, score, pace, weak_areas)

        # Append RAG reference material if available
        if rag_context:
            system_prefix += f"\n\nReference Material:\n{rag_context}\n"

        # ── Step 4: Load chat history from DB ────────────────────────────────
        history_query = {"studentId": student_id, "courseId": course_id}
        if lesson_id:
            history_query["lessonId"] = lesson_id

        recent_rows = await (
            ai_chat_history_collection
            .find(history_query)
            .sort("timestamp", -1)
            .limit(10)
            .to_list(10)
        )
        recent_rows.reverse()

        # ── Step 5: Build full prompt with history ────────────────────────────
        history_text = ""
        for entry in recent_rows:
            history_text += f"Student: {entry['studentMessage']}\nTutor: {entry['aiResponse']}\n"

        full_prompt = system_prefix + f"""

Current conversation:
{history_text}
Student: {message}
Tutor:"""

        # ── Step 6: Call Ollama (local LLM) ──────────────────────────────────
        model = await get_active_model()
        logger.info("Calling Ollama tutor model=%s", model)

        ai_text = await _call_ollama(full_prompt, model=model)
        ai_text = ai_text.strip() if ai_text else "I'm sorry, I couldn't provide a response right now."

        # ── Step 7: Persist interaction ───────────────────────────────────────
        await ai_chat_history_collection.insert_one({
            "studentId": student_id,
            "courseId": course_id,
            "lessonId": lesson_id,
            "studentMessage": message,
            "aiResponse": ai_text,
            "timestamp": datetime.utcnow().isoformat(),
            "worker_model": model,
        })

        # ── Step 8: Build history for frontend ────────────────────────────────
        latest_rows = await (
            ai_chat_history_collection
            .find(history_query)
            .sort("timestamp", -1)
            .limit(10)
            .to_list(10)
        )
        latest_rows.reverse()

        history_for_frontend = []
        for row in latest_rows:
            history_for_frontend.append({"role": "user",      "content": row["studentMessage"]})
            history_for_frontend.append({"role": "assistant", "content": row["aiResponse"]})

        return {
            "response": ai_text,
            "history": history_for_frontend,
            "studentId": student_id,
            "courseId": course_id,
        }

    @staticmethod
    async def clear_session(student_id: str, course_id: str) -> bool:
        """Clear the chat history for a specific course session."""
        try:
            await ai_chat_history_collection.delete_many(
                {"studentId": student_id, "courseId": course_id}
            )
            return True
        except Exception as e:
            logger.error("Error clearing chat session: %s", str(e))
            return False
