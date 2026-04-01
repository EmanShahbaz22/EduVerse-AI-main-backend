from __future__ import annotations
"""
ai_service.py — The AI Brain Center 🧠

FIXES APPLIED:
    1. get_llm() is called ONCE outside the retry loop (not on every attempt)
    2. MAX_RETRIES = 1 — one retry for transient (non-quota) errors only
    3. 429/RESOURCE_EXHAUSTED → raises RateLimitError immediately (no sleep)
       The router catches this and returns HTTP 503 with a Retry-After hint
       instead of hanging the HTTP connection for 60-120 seconds
    4. Non-quota transient errors: 1 retry after 5s
    5. JSON fallback parser kept — handles cases where Gemini skips the ```json fence
    6. transport="rest" kept — avoids grpcio issues on Python 3.14/Windows
    7. max_retries=0 on LangChain client — prevents silent internal retries
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
import asyncio
import re
from dotenv import load_dotenv

from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 1. Initialize the Gemini LLM
# ──────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# gemini-2.5-flash: high quota, ultra-fast model.
GEMINI_MODEL =  "gemini-2.5-flash"

# Retry config — tuned for Gemini free tier
# 429 errors are raised immediately as RateLimitError (no retry) so we don't
# hang the HTTP connection. The caller (router) returns 503 + Retry-After.
# Only transient non-quota errors (parse failures, network blips) are retried once.
MAX_RETRIES = 1          # 1 retry for non-quota errors only (2 total attempts)
OTHER_ERROR_DELAY = 5   # seconds — short wait for transient errors before 1 retry


def get_llm():
    """
    Creates and returns the Gemini LLM instance.

    FIX: This is now called ONCE per generate_lesson() call and reused
    across all retry attempts — not recreated on every attempt.

    Raises ValueError if GEMINI_API_KEY is missing, so the caller
    can surface a 503 instead of a generic 500.
    """
    # Remove debug print in production — use logger instead so it
    # respects log level config and doesn't leak keys to stdout
    logger.debug("Initializing Gemini LLM (model=%s)", GEMINI_MODEL)

    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        raise ValueError(
            
            "GEMINI_API_KEY is not set! "
            "Get a free key from https://aistudio.google.com/apikey "
            "and add it to your .env file."
        )

    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
        transport="rest",   # avoids grpcio C-extension issues on Python 3.14/Windows
        max_retries=0,
    )


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
    Attempts to fix common LLM JSON errors, specifically missing commas 
    between fields (e.g., '"field1": "val" "field2": "val"').
    """
    # Remove markdown fences if present
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw)
    
    # Fix missing commas between fields:
    # Look for a closing quote followed by a newline/space and then an opening quote (start of next key)
    # Using a lookahead to find " followed by optional whitespace and then another "
    # Pattern: " (whitespace) " -> "," (whitespace) "
    repaired = re.sub(r'(")\s*(\s*\n\s*)(")', r'\1,\2\3', raw)
    
    return repaired


# ──────────────────────────────────────────────
# 4. The main function to generate a lesson
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

    RETRY LOGIC:
        - 429/RESOURCE_EXHAUSTED: raises RateLimitError IMMEDIATELY (no sleep).
          The router catches this and returns HTTP 503 + Retry-After: 60 header.
          Do NOT retry quota errors inside a web request — it hangs the connection.
        - Other transient errors (parse failures, network blips): 1 retry after 5s
        - LLM instance is created ONCE and reused across all attempts
    """
    last_exception = None

    # FIX: Create LLM once, outside the retry loop.
    # Old code called get_llm() inside the loop — new connection on every attempt.
    llm = get_llm()
    chain = lesson_prompt | llm

    for attempt in range(1, MAX_RETRIES + 2):  # attempts: 1, 2, 3
        try:
            logger.info(
                "Calling Gemini API (attempt %d/%d) pace=%s topic=%s score=%s",
                attempt, MAX_RETRIES + 1, pace, topic, score
            )

            # USE SYNC INVOKE IN A THREAD: ainvoke is currently broken in this 
            # environment for Gemini + REST transport (TypeError: coroutine cannot be awaited)
            result = await asyncio.to_thread(chain.invoke, {
                "pace": pace,
                "topic": topic,
                "score": str(score),
                "weak_areas": weak_areas,
            })

            raw_content = result.content

            # Guard: empty response means a silent API-side failure
            if not raw_content or not raw_content.strip():
                raise ValueError(
                    "Gemini returned an empty response. "
                    "Check your API quota at https://aistudio.google.com/apikey"
                )

            # ── Parse the AI response ──
            # Try structured parser first; fall back to raw JSON extraction
            # if the AI didn't wrap output in ```json fences exactly.
            parsed = None
            try:
                parsed = lesson_output_parser.parse(raw_content)
            except Exception as parse_err:
                logger.warning(
                    "StructuredOutputParser failed (%s), trying JSON fallback…",
                    parse_err
                )
                json_match = re.search(r'\{[\s\S]*\}', raw_content)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass

                if parsed is None:
                    # Trying JSON repair as a last resort
                    logger.warning("JSON parsing failed, attempting repair…")
                    repaired_content = repair_json_string(raw_content)
                    try:
                        parsed = json.loads(repaired_content)
                    except json.JSONDecodeError:
                        # If repair also fails, throw the original parse error
                        raise ValueError(
                            f"Could not parse AI response as JSON. "
                            f"Raw content (first 200 chars): {raw_content[:200]}"
                        ) from parse_err

            # Ensure key_concepts is always a list
            if isinstance(parsed.get("key_concepts"), str):
                try:
                    parsed["key_concepts"] = json.loads(parsed["key_concepts"])
                except json.JSONDecodeError:
                    parsed["key_concepts"] = [parsed["key_concepts"]]

            # Ensure duration is always an integer
            try:
                parsed["estimated_duration_minutes"] = int(parsed["estimated_duration_minutes"])
            except (ValueError, TypeError):
                parsed["estimated_duration_minutes"] = 10  # safe default

            logger.info("Successfully generated lesson: %s", parsed.get("title", "Untitled"))
            return parsed

        except Exception as e:
            last_exception = e
            err_str = str(e).lower()
            is_rate_limit = any(
                kw in err_str for kw in ["429", "resource_exhausted", "rate limit", "quota"]
            )

            if is_rate_limit:
                # FAIL FAST on quota errors — do NOT sleep inside the request handler.
                # Sleeping 60s here hangs the HTTP connection until the client times out.
                # Instead, raise RateLimitError immediately so the router can return
                # HTTP 503 with a Retry-After hint and let the client decide when to retry.
                logger.warning(
                    "Rate limited by Gemini (attempt %d). Raising RateLimitError — "
                    "caller should retry after ~60s. Error: %s",
                    attempt, str(e)
                )
                raise RateLimitError(
                    "Gemini API quota exceeded (429). "
                    "The free tier resets per minute — please retry in 60 seconds."
                ) from e

            if attempt >= MAX_RETRIES + 1:
                # All non-quota attempts exhausted
                break

            # Non-quota transient error (network blip, parse failure) — retry once
            logger.warning(
                "Gemini call failed (attempt %d/%d), retrying in %ds. Error: %s",
                attempt, MAX_RETRIES + 1, OTHER_ERROR_DELAY, str(e)
            )
            await asyncio.sleep(OTHER_ERROR_DELAY)

    logger.error(
        "All %d Gemini attempts failed. Last error: %s",
        MAX_RETRIES + 1, str(last_exception)
    )
    raise last_exception


async def generate_base_lesson(topic: str, source_content: str) -> dict:
    """
    Generate the first/base lesson from the teacher's authored lesson notes.

    This path intentionally does NOT classify the student or assume a pace.
    It simply expands the teacher's source material into a richer lesson.
    """
    last_exception = None
    llm = get_llm()
    chain = base_lesson_prompt | llm

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            logger.info(
                "Calling Gemini API for base lesson (attempt %d/%d) topic=%s",
                attempt, MAX_RETRIES + 1, topic
            )

            result = await asyncio.to_thread(chain.invoke, {
                "topic": topic,
                "source_content": source_content,
            })

            raw_content = result.content
            if not raw_content or not raw_content.strip():
                raise ValueError(
                    "Gemini returned an empty response. "
                    "Check your API quota at https://aistudio.google.com/apikey"
                )

            parsed = None
            try:
                parsed = lesson_output_parser.parse(raw_content)
            except Exception as parse_err:
                logger.warning(
                    "StructuredOutputParser failed for base lesson (%s), trying JSON fallback...",
                    parse_err
                )
                json_match = re.search(r'\{[\s\S]*\}', raw_content)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass

                if parsed is None:
                    logger.warning("JSON parsing failed for base lesson, attempting repair...")
                    repaired_content = repair_json_string(raw_content)
                    try:
                        parsed = json.loads(repaired_content)
                    except json.JSONDecodeError:
                        raise ValueError(
                            f"Could not parse AI response as JSON. "
                            f"Raw content (first 200 chars): {raw_content[:200]}"
                        ) from parse_err

            if isinstance(parsed.get("key_concepts"), str):
                try:
                    parsed["key_concepts"] = json.loads(parsed["key_concepts"])
                except json.JSONDecodeError:
                    parsed["key_concepts"] = [parsed["key_concepts"]]

            try:
                parsed["estimated_duration_minutes"] = int(parsed["estimated_duration_minutes"])
            except (ValueError, TypeError):
                parsed["estimated_duration_minutes"] = 10

            logger.info("Successfully generated base lesson: %s", parsed.get("title", "Untitled"))
            return parsed

        except Exception as e:
            last_exception = e
            err_str = str(e).lower()
            is_rate_limit = any(
                kw in err_str for kw in ["429", "resource_exhausted", "rate limit", "quota"]
            )

            if is_rate_limit:
                logger.warning(
                    "Rate limited by Gemini during base lesson generation (attempt %d). "
                    "Raising RateLimitError. Error: %s",
                    attempt, str(e)
                )
                raise RateLimitError(
                    "Gemini API quota exceeded (429). "
                    "The free tier resets per minute - please retry in 60 seconds."
                ) from e

            if attempt >= MAX_RETRIES + 1:
                break

            logger.warning(
                "Base lesson generation failed (attempt %d/%d), retrying in %ds. Error: %s",
                attempt, MAX_RETRIES + 1, OTHER_ERROR_DELAY, str(e)
            )
            await asyncio.sleep(OTHER_ERROR_DELAY)

    logger.error(
        "All %d Gemini attempts for base lesson failed. Last error: %s",
        MAX_RETRIES + 1, str(last_exception)
    )
    raise last_exception
