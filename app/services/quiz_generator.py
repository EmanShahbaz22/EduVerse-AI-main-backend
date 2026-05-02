import json
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.schemas.adaptive_learning import QuizQuestion
from app.db.database import ai_quiz_sessions_collection
from app.services.ollama_service import (
    OllamaUnavailableError,
    OllamaGenerationError,
    _call_ollama,
    _parse_json_response,
    get_active_model,
)

logger = logging.getLogger(__name__)


def normalize_quiz_questions(raw_questions: Any) -> List[Dict[str, Any]]:
    """
    Coerce quiz questions into a proper list of question dicts.

    Some older/generated records stored `questions` as a JSON string instead of
    a native list. This helper keeps reads and writes backward-compatible.
    """
    questions = raw_questions

    if isinstance(questions, str):
        stripped = questions.strip()
        if not stripped:
            return []
        try:
            questions = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Could not parse quiz questions string as JSON.")
            return []

    if isinstance(questions, dict):
        nested = questions.get("questions")
        return normalize_quiz_questions(nested)

    if not isinstance(questions, list):
        return []

    def normalize_options(options: Any, answer: str) -> List[str]:
        if not isinstance(options, list):
            return []

        cleaned: List[str] = []
        seen = set()
        for option in options:
            if option is None:
                continue
            option_text = str(option).strip()
            if not option_text or option_text in seen:
                continue
            cleaned.append(option_text)
            seen.add(option_text)

        answer_text = str(answer).strip() if answer is not None else ""
        if answer_text and answer_text not in seen:
            cleaned.append(answer_text)
            seen.add(answer_text)

        if len(cleaned) > 4:
            if answer_text and answer_text in cleaned:
                remaining = [item for item in cleaned if item != answer_text][:3]
                return remaining + [answer_text]
            return cleaned[:4]

        return cleaned

    normalised: List[Dict[str, Any]] = []
    for item in questions:
        if isinstance(item, QuizQuestion):
            normalised.append(item.model_dump())
        elif isinstance(item, dict):
            question_text = str(item.get("question") or "").strip()
            answer_text = str(
                item.get("answer")
                or item.get("correctAnswer")
                or item.get("correct_answer")
                or ""
            ).strip()
            options = normalize_options(item.get("options", []), answer_text)

            if not question_text or len(options) < 2:
                continue

            if answer_text and answer_text not in options:
                options = normalize_options(options + [answer_text], answer_text)

            if not answer_text and options:
                answer_text = options[0]

            if answer_text not in options:
                continue

            normalised.append(
                {
                    "question": question_text,
                    "options": options,
                    "correctAnswer": answer_text,
                    "explanation": item.get("explanation") or item.get("Explanation") or None,
                }
            )

    return normalised


# ── Quiz prompt template ───────────────────────────────────────────────────────

_QUIZ_PROMPT_TEMPLATE = """\
You are an expert MCQ quiz creator. Generate a quiz on the given topic.

Topic: {topic}
Difficulty: {difficulty}
Number of Questions: {count}

CRITICAL RULES — follow exactly:
- Generate EXACTLY {count} questions. No more, no fewer.
- Each question MUST have exactly 4 short options (A, B, C, D style — keep them brief).
- Only one option is correct.
- Keep each question under 25 words. Keep each option under 10 words.
- Return ONLY a raw JSON object. No prose, no markdown fences, no explanation text.
- The JSON MUST start with {{ and end with }}.

Required JSON format:
{{
  "title": "Quiz: {topic}",
  "topic": "{topic}",
  "difficulty": "{difficulty}",
  "questions": [
    {{"question": "Question text here?", "options": ["Option A", "Option B", "Option C", "Option D"], "correct_answer": "Option A"}}
  ]
}}
"""


async def generate_ai_quiz(
    student_id: str,
    course_id: str,
    topic: str,
    difficulty: str = "intermediate",
    count: int = 5,
    tenant_id: str | None = None,
    lesson_id: str | None = None,
) -> Dict[str, Any]:
    """
    Generate an AI quiz using the active local Ollama model.
    Drop-in replacement for the old Gemini-backed quiz generation.

    Raises OllamaUnavailableError if Ollama is not running.
    Raises OllamaGenerationError on empty/invalid model output.
    """
    model = await get_active_model()
    logger.info(
        "Generating AI quiz via Ollama: model=%s student=%s topic=%s difficulty=%s count=%d",
        model, student_id, topic, difficulty, count,
    )

    prompt = _QUIZ_PROMPT_TEMPLATE.format(
        topic=topic, difficulty=difficulty, count=count
    )

    try:
        # 600 tokens per question + 500 for wrapper, minimum 2500
        # qwen2.5:3b needs generous budget to emit all questions without truncation
        quiz_predict = max(2500, count * 600 + 500)
        raw_content = await _call_ollama(
            prompt, model=model, num_predict=quiz_predict, force_json=True
        )

        parsed = _parse_json_response(raw_content, ["questions"])
        questions = normalize_quiz_questions(parsed.get("questions", []))

        if not questions:
            raise OllamaGenerationError(
                f"Quiz generation for '{topic}' produced 0 valid questions. "
                "The model response was parsed but contained no usable MCQ items. "
                "The quiz will not be saved — the adaptive pipeline will retry."
            )

        # Reject if fewer than half the requested questions came back — this
        # prevents a truncated 1-question quiz from being saved and served.
        min_required = max(1, count // 2 + 1)  # e.g. for count=5 → need at least 3
        if len(questions) < min_required:
            raise OllamaGenerationError(
                f"Quiz generation for '{topic}' only produced {len(questions)}/{count} questions "
                f"(minimum required: {min_required}). "
                "Likely caused by JSON truncation — the adaptive pipeline will retry."
            )

        quiz_session = {
            "studentId": student_id,
            "courseId": course_id,
            "topic": topic,
            "difficulty": difficulty,
            "questions": questions,
            "generatedAt": datetime.utcnow().isoformat(),
            "status": "active",
            "worker_model": model,
        }
        if tenant_id:
            quiz_session["tenantId"] = tenant_id
        if lesson_id:
            quiz_session["lessonId"] = lesson_id

        result = await ai_quiz_sessions_collection.insert_one(quiz_session)
        quiz_session["id"] = str(result.inserted_id)
        return quiz_session

    except (OllamaUnavailableError, OllamaGenerationError):
        raise
    except Exception as e:
        logger.error("Error generating AI quiz via Ollama: %s", str(e))
        raise
