import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime

import asyncio
from langchain.chains import ConversationChain
from langchain.memory import ConversationBufferWindowMemory
from langchain.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from app.db.database import ai_chat_history_collection, db
from bson import ObjectId

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. LLM Factory
# ──────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"


def get_chat_llm():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY missing.")
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
        transport="rest",
        max_retries=0,
    )


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
# 4. Chat Tutor Service
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
        full_template = system_prefix + _PROMPT_SUFFIX
        prompt = PromptTemplate(
            template=full_template,
            input_variables=["history", "input"],
        )

        # ── Step 4: Load chat history into memory ─────────────────────────────
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

        # BUG FIX: use return_messages=False so memory.load_memory_variables
        # returns a plain string instead of a list of BaseMessage objects.
        # The previous return_messages=True caused the history_for_frontend
        # loop to crash on odd-length or empty histories.
        memory = ConversationBufferWindowMemory(k=5, return_messages=False)
        for entry in recent_rows:
            memory.save_context(
                {"input": entry["studentMessage"]},
                {"output": entry["aiResponse"]},
            )

        # ── Step 5: Invoke LLM ────────────────────────────────────────────────
        llm = get_chat_llm()
        chain = ConversationChain(llm=llm, memory=memory, prompt=prompt, verbose=False)
        response = await asyncio.to_thread(chain.invoke, {"input": message})
        ai_text = response.get("response", "I'm sorry, I couldn't provide a response right now.")

        # ── Step 6: Persist interaction ───────────────────────────────────────
        await ai_chat_history_collection.insert_one({
            "studentId": student_id,
            "courseId": course_id,
            "lessonId": lesson_id,
            "studentMessage": message,
            "aiResponse": ai_text,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # ── Step 7: Build history for frontend ────────────────────────────────
        # BUG FIX: previously indexed raw BaseMessage objects as pairs using
        # range(0, len-1, 2) which crashed when history was empty or odd-length.
        # Now we re-query the DB for the latest history (including the message
        # we just saved) and build the list directly — safe and always correct.
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
