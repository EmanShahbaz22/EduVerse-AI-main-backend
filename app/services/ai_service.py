from __future__ import annotations
"""
ai_service.py — The AI Brain Center 🧠

FIXES APPLIED:
    1. Uses google.generativeai SDK directly — no LangChain ChatGoogleGenerativeAI.
       LangChain's _chat_with_retry tenacity loop retries 429 even with max_retries=0
       (stop_after_attempt(1) still allows one retry). The SDK with retry=None fires
       EXACTLY ONE HTTP request and raises immediately on 429.
    2. repair_json_string runs FIRST — strips ```json fences, escapes literal newlines.
    3. 429/ResourceExhausted → RateLimitError immediately (no sleep, no retry).
    4. LangChain kept ONLY for PromptTemplate + StructuredOutputParser (no API calls).
"""


class RateLimitError(Exception):
    """
    Raised when Gemini returns 429 / RESOURCE_EXHAUSTED.
    Caught by the router to return HTTP 503 immediately instead of hanging
    the connection for 60+ seconds with a sleep-and-retry inside the handler.
    """

import os
import json
import logging
import re
from dotenv import load_dotenv

import google.generativeai as genai
import google.api_core.exceptions

from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema

load_dotenv()
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. Configuration
# ──────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"


def _get_model(*, json_mode: bool = False, model_name: str | None = None) -> genai.GenerativeModel:
    """Returns a configured GenerativeModel. No LangChain retry wrapper.

    Args:
        json_mode: When True, sets response_mime_type='application/json'
                   so Gemini is forced to return well-formed JSON.
                   Use for lesson / quiz generation. Leave False for chat.
        model_name: Override the default model name (used for fallback).
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        raise ValueError(
            "GEMINI_API_KEY is not set! "
            "Get a free key from https://aistudio.google.com/apikey"
        )
    genai.configure(api_key=GEMINI_API_KEY)

    config_kwargs = {"temperature": 0.7}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    return genai.GenerativeModel(
        model_name=model_name or GEMINI_MODEL,
        generation_config=genai.GenerationConfig(**config_kwargs),
    )


async def _call_gemini_once(prompt_text: str, *, json_mode: bool = False) -> str:
    """
    Fires exactly ONE Gemini API call with ZERO retries for quota errors.
    retry=None disables both google-api-core and LangChain retry layers.

    - 429 ResourceExhausted  → RateLimitError immediately (no retry, no fallback).
    - 503 ServiceUnavailable  → wait 30s, retry PRIMARY model (503 ≠ quota).
      If primary still 503   → wait 15s, try FALLBACK model once.
      If fallback hits 429   → RateLimitError (don't burn more quota).

    Switching to the fallback on the first 503 was wrong: it burned the
    fallback model's separate quota when the primary was just temporarily
    busy, causing back-to-back 429s within the same minute window.

    Args:
        json_mode: When True, sets response_mime_type='application/json' so
                   Gemini is forced to return valid JSON.
    """
    import asyncio

    model = _get_model(json_mode=json_mode)
    try:
        response = await model.generate_content_async(
            prompt_text,
            request_options={"retry": None},
        )
        return response.text
    except google.api_core.exceptions.ResourceExhausted as exc:
        raise RateLimitError(
            "Gemini API quota exceeded (429). "
            "Free tier resets per minute — retry in ~60 seconds."
        ) from exc
    except google.api_core.exceptions.ServiceUnavailable:
        # 503 = server temporarily overloaded — NOT a quota error.
        # Wait 30s (quota resets per minute) then retry the SAME primary model.
        logger.warning(
            "Gemini 503 (model overloaded). Waiting 30s then retrying primary model %s…",
            GEMINI_MODEL,
        )
        await asyncio.sleep(30)
        try:
            response = await model.generate_content_async(
                prompt_text,
                request_options={"retry": None},
            )
            return response.text
        except google.api_core.exceptions.ResourceExhausted as exc:
            raise RateLimitError(
                "Gemini API quota exceeded (429) on primary model retry."
            ) from exc
        except google.api_core.exceptions.ServiceUnavailable:
            # Primary is still down after 30s — escalate to fallback as last resort.
            logger.warning(
                "Primary model still unavailable after 30s. Waiting 15s then trying fallback %s…",
                GEMINI_FALLBACK_MODEL,
            )
            await asyncio.sleep(15)
            fallback_model = _get_model(json_mode=json_mode, model_name=GEMINI_FALLBACK_MODEL)
            try:
                response = await fallback_model.generate_content_async(
                    prompt_text,
                    request_options={"retry": None},
                )
                return response.text
            except google.api_core.exceptions.ResourceExhausted as exc:
                raise RateLimitError(
                    "Gemini API quota exceeded (429) on fallback model."
                ) from exc


# ──────────────────────────────────────────────
# 2. Define what the AI's response should look like
# ──────────────────────────────────────────────

LESSON_RESPONSE_SCHEMAS = [
    ResponseSchema(name="title", description="Title of the generated lesson"),
    ResponseSchema(name="content", description="The full lesson content in Markdown format, with headings, bullet points, and examples"),
    ResponseSchema(name="difficulty", description="One of: beginner, intermediate, advanced"),
    ResponseSchema(name="estimated_duration_minutes", description="Estimated reading time in minutes (integer)"),
    ResponseSchema(name="key_concepts", description="A JSON array of 3-5 key concepts covered in this lesson"),
    ResponseSchema(name="summary", description="A 2-3 sentence summary of the lesson"),
]

lesson_output_parser = StructuredOutputParser.from_response_schemas(LESSON_RESPONSE_SCHEMAS)


# ──────────────────────────────────────────────
# 3. Create the Lesson Prompt Template
# ──────────────────────────────────────────────

LESSON_PROMPT_TEMPLATE = """You are an expert educational content creator.
Generate a personalized lesson for a student based on their learning pace and quiz performance.

**Student Learning Pace:** {pace}
**Course Topic:** {topic}
**Quiz Score:** {score}%
**Weak Areas:** {weak_areas}

**Instructions based on pace:**
- If pace is "slow": Use very simple language, lots of examples, step-by-step explanations, analogies. Keep it encouraging.
- If pace is "average": Use clear explanations with moderate examples. Include practice problems.
- If pace is "fast": Use concise explanations, introduce advanced concepts, include challenging exercises.

Generate a comprehensive lesson that helps this student improve. Focus especially on their weak areas.

CRITICAL: You must return the lesson in valid, parseable JSON format. 
DO NOT forget commas between fields. 
DO NOT include any text outside the JSON block.

{format_instructions}
"""

lesson_prompt = PromptTemplate(
    input_variables=["pace", "topic", "score", "weak_areas"],
    partial_variables={"format_instructions": lesson_output_parser.get_format_instructions()},
    template=LESSON_PROMPT_TEMPLATE,
)


BASE_LESSON_PROMPT_TEMPLATE = """You are an expert educational content creator.
Turn the teacher-provided lesson outline into a polished student-facing lesson.

**Lesson Topic:** {topic}
**Teacher Lesson Description / Notes:**
{source_content}

**Instructions:**
- Do not assume any quiz result or student pace yet.
- Preserve the teacher's intended topic and scope.
- Expand short notes into a clear, engaging lesson with explanations, examples, and a short recap.
- If the teacher content is brief, enrich it carefully without changing the topic.
- Return only valid JSON in the required format.

{format_instructions}
"""

base_lesson_prompt = PromptTemplate(
    input_variables=["topic", "source_content"],
    partial_variables={"format_instructions": lesson_output_parser.get_format_instructions()},
    template=BASE_LESSON_PROMPT_TEMPLATE,
)


# ── JSON Repair Utility ──
def repair_json_string(raw: str) -> str:
    """
    Fixes common LLM JSON output issues:

    1. Strips ```json / ``` markdown fences.
    2. Escapes literal (unescaped) newlines inside JSON string values.
       This is the #1 cause of parse failures when Gemini puts multiline
       Markdown content in the `content` field — JSON requires \\n, not a
       real line-break inside a string value.
    3. Attempts to fix missing commas between top-level fields as a last resort.
    """
    # Step 1: Strip markdown code fences
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw)
    raw = raw.strip()

    # Step 2: Escape literal newlines inside JSON string values.
    # Walk character-by-character tracking whether we are inside a string.
    # When inside a string, replace a raw '\n' with '\\n' and '\t' with '\\t'.
    result = []
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
            # Skip bare carriage returns inside strings
            continue
        if in_string and ch == '\t':
            result.append('\\t')
            continue
        result.append(ch)
    repaired = ''.join(result)

    return repaired


# ── Shared parse helper ──

def _extract_fields_by_regex(raw: str) -> dict | None:
    """
    Last-resort parser: extract each known field individually via regex.
    
    When Gemini puts unescaped quotes inside the `content` field
    (e.g. … some "quoted word" inside markdown …) the JSON is malformed
    and standard parsers fail.  This function grabs each field value by
    matching the key name and reading the value between the surrounding
    structure markers.
    """
    fields = {}
    
    # Simple string fields — grab value between "key": "..." 
    # using a non-greedy match that stops at the next top-level key or closing brace.
    simple_keys = ["title", "difficulty", "summary"]
    for key in simple_keys:
        match = re.search(
            rf'"{key}"\s*:\s*"((?:[^"\\]|\\.){{0,500}})"',
            raw,
            re.DOTALL,
        )
        if match:
            fields[key] = match.group(1).replace('\\"', '"').replace('\\n', '\n')
    
    # estimated_duration_minutes — numeric
    dur_match = re.search(r'"estimated_duration_minutes"\s*:\s*(\d+)', raw)
    if dur_match:
        fields["estimated_duration_minutes"] = int(dur_match.group(1))
    
    # key_concepts — JSON array
    kc_match = re.search(r'"key_concepts"\s*:\s*(\[.*?\])', raw, re.DOTALL)
    if kc_match:
        try:
            fields["key_concepts"] = json.loads(kc_match.group(1))
        except json.JSONDecodeError:
            fields["key_concepts"] = [kc_match.group(1)]
    
    # content — THE PROBLEMATIC FIELD.
    # Strategy: find "content": " and then grab everything until the next
    # top-level key pattern ("difficulty": or "estimated_duration_minutes":)
    content_match = re.search(
        r'"content"\s*:\s*"([\s\S]*?)"\s*,\s*"(?:difficulty|estimated_duration_minutes|key_concepts|summary)"',
        raw,
    )
    if content_match:
        content_val = content_match.group(1)
        # Unescape the common escape sequences
        content_val = content_val.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
        fields["content"] = content_val
    elif "title" in fields:
        # Absolute fallback: grab everything between "content": " and the last few fields
        content_start = raw.find('"content"')
        if content_start != -1:
            # Find the opening quote of the content value
            colon_pos = raw.find(':', content_start + 9)
            if colon_pos != -1:
                quote_pos = raw.find('"', colon_pos + 1)
                if quote_pos != -1:
                    # Find where the next field starts (scan backwards from end)
                    for end_key in ['"summary"', '"key_concepts"', '"estimated_duration_minutes"', '"difficulty"']:
                        end_pos = raw.rfind(end_key)
                        if end_pos > quote_pos:
                            # Go back past the comma and closing quote
                            snippet = raw[quote_pos + 1:end_pos].rstrip().rstrip(',').rstrip().rstrip('"')
                            snippet = snippet.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
                            fields["content"] = snippet
                            break
    
    if "title" in fields and "content" in fields:
        return fields
    return None


def _parse_lesson_response(raw_content: str) -> dict:
    """
    Robust parser with multiple fallback layers:
    
    1. json.loads (strict) on cleaned input
    2. json.loads (strict=False) — allows control characters
    3. Field-by-field regex extraction — handles unescaped quotes
       inside the content field that break standard JSON parsing
    4. LangChain StructuredOutputParser + repair_json_string
    
    No API calls are made here.
    """
    repaired = repair_json_string(raw_content)
    parsed = None

    # ── Layer 1: Direct json.loads ──
    json_text = repaired
    json_match = re.search(r'\{[\s\S]*\}', repaired)
    if json_match:
        json_text = json_match.group()
    
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        pass

    # ── Layer 2: json.loads with strict=False (allows control chars) ──
    if parsed is None:
        try:
            parsed = json.loads(json_text, strict=False)
        except json.JSONDecodeError:
            pass

    # ── Layer 3: Field-by-field regex extraction ──
    if parsed is None:
        logger.warning("Standard JSON parsing failed, trying field-by-field regex extraction")
        parsed = _extract_fields_by_regex(raw_content)

    # ── Layer 4: LangChain structured parser (original approach) ──
    if parsed is None:
        try:
            parsed = lesson_output_parser.parse(repaired)
        except Exception as parse_err:
            logger.warning("All parse strategies failed. Last error: %s", parse_err)
            raise ValueError(
                f"Could not parse AI response as JSON even after repair. "
            ) from parse_err

    # ── Safe defaults for every field the schema requires ──
    # Use .get() so a partially-extracted regex result never causes a KeyError
    # downstream in lesson_generator when it reads these fields.

    # key_concepts: must be a list
    kc = parsed.get("key_concepts", [])
    if isinstance(kc, str):
        try:
            kc = json.loads(kc)
        except json.JSONDecodeError:
            kc = [kc]
    parsed["key_concepts"] = kc if isinstance(kc, list) else []

    # estimated_duration_minutes: must be an int
    raw_duration = parsed.get("estimated_duration_minutes", None)
    try:
        parsed["estimated_duration_minutes"] = int(raw_duration) if raw_duration is not None else 10
    except (ValueError, TypeError):
        parsed["estimated_duration_minutes"] = 10

    # Ensure remaining string fields always exist (never KeyError downstream)
    parsed.setdefault("title", "Untitled Lesson")
    parsed.setdefault("content", "")
    parsed.setdefault("difficulty", "intermediate")
    parsed.setdefault("summary", "")

    return parsed


# ──────────────────────────────────────────────
# 4. Generate functions (1 API call each, no retries)
# ──────────────────────────────────────────────

async def generate_lesson(pace: str, topic: str, score: float, weak_areas: str) -> dict:
    """
    Generate a personalized lesson using LangChain + Gemini.

    Args:
        pace: "slow", "average", or "fast" (from the classifier)
        topic: The course/subject topic (e.g., "Fractions in Mathematics")
        score: The student's quiz score percentage (e.g., 45.0)
        weak_areas: Comma-separated weak areas (e.g., "addition of fractions, simplification")

    Returns:
        dict with keys: title, content, difficulty, estimated_duration_minutes,
                        key_concepts, summary
    ONE Gemini API call is made. 429 raises RateLimitError immediately.
    """
    logger.info(
        "Calling Gemini API (1 call, no retry) pace=%s topic=%s score=%s",
        pace, topic, score
    )
    prompt_text = lesson_prompt.format(
        pace=pace, topic=topic, score=str(score), weak_areas=weak_areas
    )
    raw_content = await _call_gemini_once(prompt_text, json_mode=True)

    if not raw_content or not raw_content.strip():
        raise ValueError("Gemini returned an empty response.")

    parsed = _parse_lesson_response(raw_content)
    logger.info("Successfully generated lesson: %s", parsed.get("title", "Untitled"))
    return parsed



async def generate_base_lesson(topic: str, source_content: str) -> dict:
    """
    Generate the first/base lesson from the teacher's authored lesson notes.
    ONE Gemini API call is made. 429 raises RateLimitError immediately.
    """
    logger.info(
        "Calling Gemini API for base lesson (1 call, no retry) topic=%s", topic
    )
    prompt_text = base_lesson_prompt.format(
        topic=topic, source_content=source_content
    )
    raw_content = await _call_gemini_once(prompt_text, json_mode=True)

    if not raw_content or not raw_content.strip():
        raise ValueError("Gemini returned an empty response.")

    parsed = _parse_lesson_response(raw_content)
    logger.info("Successfully generated base lesson: %s", parsed.get("title", "Untitled"))
    return parsed
