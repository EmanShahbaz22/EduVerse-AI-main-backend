"""
ollama_service.py — Local LLM Orchestrator via Ollama

Replaces: app/services/ai_service.py (Gemini API calls)

Architecture:
    - 3 worker models (one active at a time, chosen by Super Admin via MongoDB)
        phi3.5       → lesson generation
        qwen2.5:3b   → MCQ / quiz generation
        llama3.2:3b  → AI tutor Q&A
    - All run locally via Ollama at http://localhost:11434
    - active_worker_model is read from MongoDB `config` collection on each call
      so the Super Admin can switch models live with zero restart

Public API (drop-in replacement for ai_service.py):
    generate_lesson(pace, topic, score, weak_areas) → dict
    generate_base_lesson(topic, source_content)     → dict
    generate_quiz(topic, difficulty, count)         → dict
    chat_tutor(message, context)                    → str

Critical constants (per spec):
    temperature   : 0.3  (factual, not creative)
    Ollama URL    : http://localhost:11434
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.db.database import config_collection

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
LLM_TEMPERATURE = 0.3

# Model aliases — must match `ollama pull` names exactly
WORKER_MODELS = {
    "phi3.5":      "phi3.5",        # best for lesson generation
    "qwen2.5:3b":  "qwen2.5:3b",    # best for MCQ generation
    "llama3.2:3b": "llama3.2:3b",   # best for AI tutor
}
DEFAULT_MODEL = "phi3.5"

# Config document ID in MongoDB
CONFIG_ACTIVE_MODEL_ID = "active_worker_model"


# ── Model resolver ─────────────────────────────────────────────────────────────

async def get_active_model() -> str:
    """
    Read the active worker model name from MongoDB config collection.
    Falls back to DEFAULT_MODEL if not set or DB unavailable.
    """
    try:
        doc = await config_collection.find_one({"_id": CONFIG_ACTIVE_MODEL_ID})
        if doc and doc.get("value") in WORKER_MODELS:
            return doc["value"]
    except Exception as e:
        logger.warning("Could not read active_worker_model from DB: %s. Using default.", e)
    return DEFAULT_MODEL


async def set_active_model(model_name: str, updated_by: str = "superadmin") -> None:
    """
    Persist the active worker model to MongoDB.
    Called by the Super Admin model selector endpoint.
    """
    if model_name not in WORKER_MODELS:
        raise ValueError(f"Unknown model '{model_name}'. Choose from: {list(WORKER_MODELS)}")
    await config_collection.update_one(
        {"_id": CONFIG_ACTIVE_MODEL_ID},
        {"$set": {"value": model_name, "updated_by": updated_by}},
        upsert=True,
    )
    logger.info("Active worker model set to '%s' by %s", model_name, updated_by)


# ── Core Ollama HTTP caller ────────────────────────────────────────────────────

async def _call_ollama(
    prompt: str,
    model: str | None = None,
    num_predict: int = 4096,
    force_json: bool = False,
) -> str:
    """
    Send a single generate request to the local Ollama server.

    Args:
        prompt:      Full prompt string (system instructions + user content).
        model:       Ollama model name. If None, reads from MongoDB config.
        num_predict: Max tokens to generate. Tune per call type to avoid
                     truncation — quizzes need far fewer tokens than lessons.
        force_json:  If True, sets Ollama's ``format`` field to ``"json"``
                     which constrains the model to emit valid JSON tokens.
                     Use for structured MCQ / quiz generation.

    Returns:
        The raw text response from the model.

    Raises:
        OllamaUnavailableError: Ollama server is not running.
        OllamaGenerationError:  Model returned an error or empty response.
    """
    if model is None:
        model = await get_active_model()

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": num_predict,
            "num_ctx": 8192,   # larger context window — input+output budget
        },
    }
    if force_json:
        payload["format"] = "json"  # Ollama native JSON mode

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data.get("response", "").strip()
            if not text:
                raise OllamaGenerationError("Ollama returned an empty response.")
            return text
    except httpx.ConnectError:
        raise OllamaUnavailableError(
            "Ollama server is not running at http://localhost:11434. "
            "Start it with: ollama serve"
        )
    except httpx.HTTPStatusError as e:
        raise OllamaGenerationError(f"Ollama HTTP error: {e.response.status_code} — {e.response.text}")


class OllamaUnavailableError(Exception):
    """Raised when the local Ollama server is not reachable."""

class OllamaGenerationError(Exception):
    """Raised when Ollama returns an error or empty response."""


# ── JSON repair + parse ────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    """Strip markdown fences and escape raw newlines inside JSON strings."""
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw).strip()

    result: list[str] = []
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch == '\n':
            result.append('\\n')
            continue
        if in_string and ch == '\r':
            continue
        if in_string and ch == '\t':
            result.append('\\t')
            continue
        result.append(ch)
    return ''.join(result)


def _extract_partial_questions(raw: str) -> list:
    """
    Walk the raw string and pull out every complete JSON question object
    (i.e. has 'question', 'options', and 'correct_answer' / 'answer' keys)
    even when the surrounding array was truncated before the closing ']'.

    Uses separate depth counters for braces and brackets so they don't
    interfere with each other.
    """
    questions: list = []
    questions_pos = raw.find('"questions"')
    if questions_pos == -1:
        return questions

    bracket_start = raw.find('[', questions_pos)
    if bracket_start == -1:
        return questions

    brace_depth = 0   # tracks nesting of { }
    in_array = False  # True once we pass the opening '['
    in_string = False
    escape_next = False
    obj_start = -1

    for i, ch in enumerate(raw[bracket_start:], bracket_start):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == '[':
            in_array = True
            continue
        if ch == ']' and brace_depth == 0:
            # Closed the questions array — stop walking
            break

        if ch == '{':
            brace_depth += 1
            if brace_depth == 1 and in_array:
                obj_start = i   # start of a top-level question object
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and obj_start != -1:
                # Closed a question object — try to parse it
                try:
                    obj = json.loads(raw[obj_start:i + 1], strict=False)
                    if isinstance(obj, dict) and 'question' in obj and 'options' in obj:
                        questions.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = -1

    return questions


def _parse_json_response(raw: str, required_keys: list[str]) -> dict:
    """
    Multi-layer JSON parser.
    Layer 1: direct json.loads after fence stripping.
    Layer 2: json.loads(strict=False).
    Layer 3: extract first JSON object with regex.
    Layer 4 (rescue): output was truncated or malformed.
      - 4a (quiz):   extract partial question objects.
      - 4b (lesson): extract content field value.
      - 4c (prose):  use raw text as content.
    """
    repaired = _repair_json(raw)

    # Layer 1 — strict parse
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return _apply_defaults(parsed)
    except json.JSONDecodeError:
        pass

    # Layer 2 — lenient parse
    try:
        parsed = json.loads(repaired, strict=False)
        if isinstance(parsed, dict):
            return _apply_defaults(parsed)
    except json.JSONDecodeError:
        pass

    # Layer 3 — regex extract first {...}
    match = re.search(r'\{[\s\S]*\}', repaired)
    if match:
        try:
            parsed = json.loads(match.group(), strict=False)
            if isinstance(parsed, dict):
                return _apply_defaults(parsed)
        except json.JSONDecodeError:
            pass

    # Layer 4 — rescue
    logger.warning("All JSON parse layers failed. Attempting field rescue. Raw: %.200s", raw)
    raw_stripped = raw.strip()

    if raw_stripped.startswith('{'):
        title_re = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_stripped)
        rescued_title = "AI Generated Lesson"
        if title_re:
            rescued_title = title_re.group(1).replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')

        # 4a — Quiz rescue: extract partial question objects
        if "questions" in required_keys:
            questions = _extract_partial_questions(raw_stripped)
            logger.warning("Layer 4 quiz rescue: title=%r recovered=%d", rescued_title, len(questions))
            return _apply_defaults({
                "title": rescued_title,
                "topic": rescued_title.replace("Quiz: ", ""),
                "questions": questions,
            })

        # 4b — Lesson rescue: extract content field
        rescued_content = ""
        content_re = re.search(r'"content"\s*:\s*"([\s\S]*)', raw_stripped)
        if content_re:
            raw_content = content_re.group(1)
            raw_content = re.sub(
                r'",\s*"(?:difficulty|estimated_duration_minutes|key_concepts|summary)"[\s\S]*$',
                '', raw_content
            )
            raw_content = re.sub(r'"\s*\}\s*$', '', raw_content)
            rescued_content = (
                raw_content.replace('\\n', '\n').replace('\\t', '\t')
                .replace('\\"', '"').replace('\\\\', '\\')
            )
        if not rescued_content.strip():
            rescued_content = f"```json\n{raw_stripped}\n```"
        logger.warning("Layer 4 lesson rescue: title=%r content_len=%d", rescued_title, len(rescued_content))
        return _apply_defaults({"title": rescued_title, "content": rescued_content})

    # ── Case B: plain Markdown prose ───────────────────────────────────
    title_match = re.search(r'^#+ (.+)$', raw_stripped, re.MULTILINE)
    rescued_title = title_match.group(1).strip() if title_match else "AI Generated Lesson"
    return _apply_defaults({"title": rescued_title, "content": raw_stripped})


def _apply_defaults(parsed: dict) -> dict:
    """Ensure all schema fields exist with safe defaults."""
    parsed.setdefault("title", "Untitled Lesson")
    
    # Safely handle 'content' if the model returns a list of strings instead of one string
    raw_content = parsed.get("content") or parsed.get("text") or "Content unavailable."
    if isinstance(raw_content, list):
        parsed["content"] = "\n\n".join(str(c) for c in raw_content)
    else:
        parsed["content"] = str(raw_content)

    parsed.setdefault("difficulty", "intermediate")
    parsed.setdefault("summary", "")
    parsed.setdefault("key_concepts", [])

    # key_concepts must be a list
    kc = parsed.get("key_concepts", [])
    if isinstance(kc, str):
        try:
            kc = json.loads(kc)
        except Exception:
            kc = [kc]
    parsed["key_concepts"] = kc if isinstance(kc, list) else []

    # estimated_duration_minutes must be int
    raw_dur = parsed.get("estimated_duration_minutes", None)
    try:
        parsed["estimated_duration_minutes"] = int(raw_dur) if raw_dur is not None else 10
    except (ValueError, TypeError):
        parsed["estimated_duration_minutes"] = 10

    return parsed


# ── Prompt templates ──────────────────────────────────────────────────────────

_LESSON_PROMPT = """\
You are an expert educational content creator. Generate a highly detailed, comprehensive, and engaging lesson.

Student Learning Pace: {pace}
Course Topic: {topic}
Quiz Score: {score}%
Weak Areas: {weak_areas}

Pace instructions:
- slow: use very simple language, lots of analogies, step-by-step walkthroughs, and real-world examples.
- average: provide clear explanations, moderate examples, and practice problems with solutions.
- fast: be concise but thorough, dive into advanced concepts, deeper insights, and challenging exercises.

Focus heavily on the student's weak areas and ensure deep, comprehensive coverage of the topic.

CRITICAL CONTENT REQUIREMENTS — YOU MUST FOLLOW ALL OF THESE STRICTLY:
1. Length: You MUST write a very long, detailed lesson in the "content" field (minimum 1000 words). Do NOT write a brief summary.
2. Structure: You MUST use Markdown formatting with exactly these 4 headings: 
   ## Introduction
   ## Core Concepts
   ## Worked Examples
   ## Key Takeaways
3. Detail: Each section MUST have at least 3 detailed paragraphs and bullet points.
4. Examples: You MUST include at least 3 fully worked examples with step-by-step code or solutions.
5. Completeness: Do NOT stop early. Do NOT summarize. Write out the full lesson text explicitly.

You MUST respond with ONLY a raw JSON object. Do not include markdown fences around the JSON.
The JSON object MUST start with {{ and end with }}. Fill in every field.

JSON format:
{{
  "title": "A catchy title for the lesson",
  "difficulty": "beginner/intermediate/advanced",
  "estimated_duration_minutes": 20,
  "key_concepts": ["concept1", "concept2", "concept3", "concept4"],
  "summary": "A 2-3 sentence brief summary of the lesson.",
  "content": "## Introduction\\n\\n(Write at least 3 paragraphs here...)\\n\\n## Core Concepts\\n\\n(Write deep explanations here...)\\n\\n## Worked Examples\\n\\n(Provide 3 detailed examples here...)\\n\\n## Key Takeaways\\n\\n(Summarize the main points...)"
}}
"""

_BASE_LESSON_PROMPT = """\
You are an expert educational content creator.
Expand the teacher's lesson notes into a highly detailed, comprehensive, polished, student-facing lesson.

Lesson Topic: {topic}
Teacher Notes:
{source_content}

Instructions:
- Do not assume any quiz result or student pace.
- Preserve the teacher's intended topic and scope.
- Expand the brief notes into clear, engaging, and deeply detailed content.

CRITICAL CONTENT REQUIREMENTS — YOU MUST FOLLOW ALL OF THESE STRICTLY:
1. Length: You MUST write a very long, detailed lesson in the "content" field (minimum 1000 words). Do NOT write a brief summary.
2. Structure: You MUST use Markdown formatting with exactly these 4 headings:
   ## Introduction
   ## Core Concepts
   ## Examples & Illustrations
   ## Summary
3. Detail: Each section MUST have at least 3 detailed paragraphs and bullet points.
4. Examples: You MUST include at least 3 concrete examples or case studies.
5. Completeness: Do NOT stop early. Do NOT summarize. Write out the full lesson text explicitly.

You MUST respond with ONLY a raw JSON object. Do not include markdown fences around the JSON.
The JSON object MUST start with {{ and end with }}. Fill in every field.

JSON format:
{{
  "title": "A catchy title for the lesson",
  "difficulty": "beginner/intermediate/advanced",
  "estimated_duration_minutes": 20,
  "key_concepts": ["concept1", "concept2", "concept3", "concept4"],
  "summary": "A 2-3 sentence brief summary of the lesson.",
  "content": "## Introduction\\n\\n(Write at least 3 paragraphs here...)\\n\\n## Core Concepts\\n\\n(Write deep explanations here...)\\n\\n## Examples & Illustrations\\n\\n(Provide 3 detailed examples here...)\\n\\n## Summary\\n\\n(Summarize the main points...)"
}}
"""

_QUIZ_PROMPT = """\
You are an expert MCQ quiz creator. Generate a quiz on the given topic.

Topic: {topic}
Difficulty: {difficulty}
Number of Questions: {count}

Rules:
- Each question must have exactly 4 options.
- Only one option is correct.
- Vary question styles: factual, conceptual, application.
- Return ONLY valid JSON in this exact format:
{{
  "title": "Quiz: {topic}",
  "topic": "{topic}",
  "difficulty": "{difficulty}",
  "questions": [
    {{
      "question": "string",
      "options": ["A", "B", "C", "D"],
      "correct_answer": "A",
      "explanation": "Why A is correct"
    }}
  ]
}}
Do NOT include any text outside the JSON block.
"""

_TUTOR_PROMPT = """\
You are a helpful AI study assistant for students on the EduVerse learning platform.
Answer the student's question clearly and concisely.

Course Context: {context}
Student Question: {message}

Rules:
- Be encouraging and educational.
- Use simple language appropriate for students.
- Keep answers focused and relevant to the course topic.
- If you don't know, say so honestly.
"""


# ── Public API (drop-in replacement for ai_service.py) ────────────────────────

async def generate_lesson(pace: str, topic: str, score: float, weak_areas: str, model_override: str | None = None) -> dict:
    """
    Generate a personalized adaptive lesson using the active worker model.
    Drop-in replacement for ai_service.generate_lesson().
    Pass model_override to force a specific model (e.g. during benchmarking).
    """
    model = model_override if model_override else await get_active_model()
    logger.info("Generating adaptive lesson: model=%s pace=%s topic=%s score=%.1f", model, pace, topic, score)

    prompt = _LESSON_PROMPT.format(
        pace=pace, topic=topic, score=score, weak_areas=weak_areas or "general review"
    )
    t0 = time.time()
    raw = await _call_ollama(prompt, model=model)
    elapsed = int((time.time() - t0) * 1000)
    logger.info("Lesson generated in %dms", elapsed)

    parsed = _parse_json_response(raw, ["title", "content"])
    return parsed


async def generate_base_lesson(topic: str, source_content: str) -> dict:
    """
    Generate the first/base lesson from teacher-authored notes.
    Drop-in replacement for ai_service.generate_base_lesson().
    """
    model = await get_active_model()
    logger.info("Generating base lesson: model=%s topic=%s", model, topic)

    prompt = _BASE_LESSON_PROMPT.format(topic=topic, source_content=source_content)
    t0 = time.time()
    raw = await _call_ollama(prompt, model=model)
    elapsed = int((time.time() - t0) * 1000)
    logger.info("Base lesson generated in %dms", elapsed)

    return _parse_json_response(raw, ["title", "content"])


async def generate_quiz(topic: str, difficulty: str = "medium", count: int = 5, model_override: str | None = None) -> dict:
    """
    Generate an MCQ quiz using the active worker model.
    Pass model_override to force a specific model (e.g. during benchmarking).
    """
    model = model_override if model_override else await get_active_model()
    logger.info("Generating quiz: model=%s topic=%s difficulty=%s count=%d", model, topic, difficulty, count)

    prompt = _QUIZ_PROMPT.format(topic=topic, difficulty=difficulty, count=count)
    t0 = time.time()
    quiz_predict = max(1000, count * 350 + 300)
    raw = await _call_ollama(prompt, model=model, num_predict=quiz_predict, force_json=True)
    elapsed = int((time.time() - t0) * 1000)
    logger.info("Quiz generated in %dms (num_predict=%d)", elapsed, quiz_predict)

    parsed = _parse_json_response(raw, ["questions"])
    if not isinstance(parsed.get("questions"), list):
        parsed["questions"] = []
    return parsed


async def chat_tutor(message: str, context: str = "", model_override: str | None = None) -> str:
    """
    Answer a student question using the active worker model.
    Pass model_override to force a specific model (e.g. during benchmarking).
    """
    model = model_override if model_override else await get_active_model()
    logger.info("Tutor chat: model=%s", model)

    prompt = _TUTOR_PROMPT.format(message=message, context=context or "general course content")
    response = await _call_ollama(prompt, model=model)
    return response


# ── Model health check ─────────────────────────────────────────────────────────

async def check_ollama_health() -> dict[str, Any]:
    """
    Check if Ollama is running and list available models.
    Used by the Super Admin model selector to show which models are pulled.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            available = [m["name"] for m in data.get("models", [])]
            return {
                "status": "online",
                "available_models": available,
                "active_model": await get_active_model(),
            }
    except Exception as e:
        return {
            "status": "offline",
            "error": str(e),
            "available_models": [],
            "active_model": None,
        }


async def run_model_benchmark(test_prompts: list[dict]) -> dict[str, Any]:
    """
    Run benchmark prompts across all 3 worker models SEQUENTIALLY.
    Each model is tested with an explicit model_override so results reflect
    that specific model — not whichever is currently set as active in MongoDB.

    After each successful generation, Layer 1 + Layer 2 validation is run and
    saved to validation_results_collection so the leaderboard shows real scores
    for all 3 models (not just the one that happened to be active during student use).
    reference_chunks=[] is passed for Layer 2 (no course-specific RAG context),
    giving all models a fair Layer 1 comparison (ROUGE, BERTScore, structure).
    Sequential (never parallel) to avoid OOM on limited RAM.
    """
    from app.services.validation_pipeline import _validate_once, _save_validation_result  # type: ignore

    results: dict[str, Any] = {}

    for model_name in WORKER_MODELS:
        logger.info("Benchmarking model: %s (%d prompts)", model_name, len(test_prompts))
        model_results = []

        for prompt_cfg in test_prompts:
            task = prompt_cfg.get("task_type", "lesson")
            topic = prompt_cfg.get("topic", prompt_cfg.get("question", "General Knowledge"))
            t0 = time.time()
            generated_text = ""
            try:
                if task == "lesson":
                    content_dict = await generate_lesson(
                        "average", topic, 70.0, "general review",
                        model_override=model_name
                    )
                    generated_text = content_dict.get("content", str(content_dict))
                elif task == "mcq":
                    content_dict = await generate_quiz(
                        topic, prompt_cfg.get("difficulty", "medium"), prompt_cfg.get("count", 3),
                        model_override=model_name
                    )
                    generated_text = str(content_dict)
                else:  # tutor
                    generated_text = await chat_tutor(
                        prompt_cfg.get("question", topic),
                        model_override=model_name
                    )
                    content_dict = {"response": generated_text}

                latency = int((time.time() - t0) * 1000)

                # ── Validate and save to leaderboard collection ────────────────
                # reference_chunks=[] → Layer 2 = 0 for all models equally so
                # Layer 1 scores (ROUGE, BERTScore, structure) are comparable.
                try:
                    val_result = _validate_once(generated_text, topic, task, reference_chunks=[])
                    await _save_validation_result(
                        result=val_result,
                        content=generated_text,
                        topic=topic,
                        task_type=task,
                        worker_model=model_name,
                        student_id="benchmark",
                        tenant_id="benchmark",
                        latency_ms=latency,
                        attempt=1,
                    )
                    logger.info(
                        "Benchmark validation saved: model=%s score=%d verdict=%s",
                        model_name, val_result["final_score"], val_result["final_verdict"],
                    )
                except Exception as ve:
                    logger.warning("Benchmark validation save failed for model=%s: %s", model_name, ve)
                # ──────────────────────────────────────────────────────────────

                model_results.append({
                    "topic": topic,
                    "task": task,
                    "latency_ms": latency,
                    "success": True,
                    "content_length": len(generated_text),
                })
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                model_results.append({
                    "topic": topic,
                    "task": task,
                    "latency_ms": latency,
                    "success": False,
                    "error": str(e),
                })
                logger.warning("Benchmark failed for model=%s topic=%s: %s", model_name, topic, e)

        successful = [r for r in model_results if r["success"]]
        results[model_name] = {
            "pass_rate": len(successful) / len(model_results) if model_results else 0,
            "avg_latency_ms": int(sum(r["latency_ms"] for r in successful) / len(successful)) if successful else 0,
            "total_prompts": len(model_results),
            "details": model_results,
        }

    return results

