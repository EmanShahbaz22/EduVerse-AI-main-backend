from __future__ import annotations
"""
ai_service.py — The AI Brain Center 🧠

WHY THIS FILE EXISTS:
    This is like a "translator" between our app and Google's Gemini AI.
    Instead of calling Gemini directly (which gives messy text), we use
    LangChain to:
    1. Send a well-structured PROMPT (like a fill-in-the-blank template)
    2. Force the AI to return proper JSON (not random text)
    3. Make it easy to reuse the same setup for lessons, quizzes, chat, etc.

WHAT IT DOES:
    - Initializes the Gemini LLM (the AI model)
    - Creates a "lesson generation chain" (prompt → AI → structured JSON)
    - Other members will import from here to build their own chains

FIX APPLIED (LangChain-Gemini 404 Resolution):
    Root cause: langchain-google-genai defaults to the v1beta endpoint, which
    rejects the "-latest" model name suffix after LangChain normalizes it.
    Three changes were made to resolve this:
      1. transport="rest"         — bypasses grpcio which has broken C-extensions
                                    on Python 3.14 / Windows 11.
      2. client_options           — forces routing to the stable v1 endpoint
                                    instead of v1beta.
      3. model="gemini-2.0-flash" — uses a pinned, versioned model name
                                    that v1 accepts reliably (no "-latest" suffix
                                    that LangChain strips unpredictably).
"""

import os
import json
import logging
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

# FIX: Use a pinned, versioned model name.
# "gemini-2.0-flash-latest" gets normalized by LangChain into
# "models/gemini-2.0-flash" which the v1beta endpoint rejects with 404.
# "gemini-2.0-flash" is stable and accepted by the v1 endpoint.
GEMINI_MODEL = "gemini-2.0-flash"

# FIX: Route to the stable v1 API endpoint instead of LangChain's
# default v1beta endpoint. v1beta inconsistently resolves versioned
# model names, especially after LangChain's internal name normalization.
GEMINI_CLIENT_OPTIONS = {
    "api_endpoint": "https://generativelanguage.googleapis.com"
}


def get_llm():
    """
    Creates and returns the Gemini LLM instance.

    WHY a function instead of a global variable?
    - So we can call it fresh each time (avoids stale connections)
    - Makes testing easier (we can mock this function)

    FIXES APPLIED:
    - transport="rest": Avoids grpcio C-extension issues on Python 3.14/Windows.
    - client_options: Forces the v1 (stable) API endpoint.
    - model name pinned to "gemini-2.0-flash": No suffix stripping by LangChain.
    """
    print(f"DEBUG: GEMINI_API_KEY prefix: {GEMINI_API_KEY[:5] if GEMINI_API_KEY else 'NOT SET'}...")
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
        transport="rest",                    # FIX 1: avoid grpc on Python 3.14
        client_options=GEMINI_CLIENT_OPTIONS, # FIX 2: force v1 endpoint
        convert_system_message_to_human=True,
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

{format_instructions}
"""

lesson_prompt = PromptTemplate(
    input_variables=["pace", "topic", "score", "weak_areas"],
    partial_variables={"format_instructions": lesson_output_parser.get_format_instructions()},
    template=LESSON_PROMPT_TEMPLATE,
)


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
    """
    try:
        llm = get_llm()

        # pipe operator: fill prompt → send to Gemini → get response
        chain = lesson_prompt | llm

        result = await chain.ainvoke({
            "pace": pace,
            "topic": topic,
            "score": str(score),
            "weak_areas": weak_areas,
        })

        # result is an AIMessage object — use .content not result["text"]
        raw_content = result.content

        # Guard: if the model returned an empty response (can happen on
        # transient API errors), raise early with a clear message.
        if not raw_content or not raw_content.strip():
            raise ValueError(
                "Gemini returned an empty response. "
                "Check your API quota at https://aistudio.google.com/apikey"
            )

        parsed = lesson_output_parser.parse(raw_content)

        # Make sure key_concepts is a list (sometimes AI returns a string)
        if isinstance(parsed.get("key_concepts"), str):
            try:
                parsed["key_concepts"] = json.loads(parsed["key_concepts"])
            except json.JSONDecodeError:
                parsed["key_concepts"] = [parsed["key_concepts"]]

        # Make sure duration is an integer
        try:
            parsed["estimated_duration_minutes"] = int(parsed["estimated_duration_minutes"])
        except (ValueError, TypeError):
            parsed["estimated_duration_minutes"] = 10  # default

        logger.info("Successfully generated lesson: %s", parsed.get("title", "Untitled"))
        return parsed

    except Exception as e:
        logger.error("Failed to generate lesson: %s", str(e))
        raise