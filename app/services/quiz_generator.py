import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from langchain_google_genai import ChatGoogleGenerativeAI

from app.schemas.adaptive_learning import QuizQuestion
from app.db.database import ai_quiz_sessions_collection
from app.services.ai_service import RateLimitError

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

    normalised: List[Dict[str, Any]] = []
    for item in questions:
        if isinstance(item, QuizQuestion):
            normalised.append(item.model_dump())
        elif isinstance(item, dict):
            normalised.append(item)

    return normalised

# ──────────────────────────────────────────────
# 1. Initialize the Gemini LLM
# ──────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

def get_llm():
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY missing")
        raise ValueError("GEMINI_API_KEY is not set.")
    
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
        transport="rest"
    )

# ──────────────────────────────────────────────
# 2. Output Parser
# ──────────────────────────────────────────────

QUIZ_RESPONSE_SCHEMAS = [
    ResponseSchema(name="questions", description="A JSON array of multiple-choice questions. Each question MUST have: question, options (array of 4), correctAnswer, and explanation."),
]

quiz_output_parser = StructuredOutputParser.from_response_schemas(QUIZ_RESPONSE_SCHEMAS)

# ──────────────────────────────────────────────
# 3. Prompt Template
# ──────────────────────────────────────────────

QUIZ_PROMPT_TEMPLATE = """
You are an expert educational content creator. Your task is to generate a high-quality Multiple Choice Quiz (MCQ) based on a specific topic.

Topic: {topic}
Difficulty: {difficulty}
Number of Questions: {count}

Rules:
1. Each question must have exactly 4 options.
2. The 'correctAnswer' must be one of the strings in the 'options' array.
3. Provide a helpful 'explanation' for each correct answer.
4. Ensure the questions match the student's level ({difficulty}).
5. Return the response in the specified JSON format.

{format_instructions}
"""

# ──────────────────────────────────────────────
# 4. Generator Function
# ──────────────────────────────────────────────

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
    Generates an AI quiz and saves it to the database.
    """
    logger.info(f"Generating AI quiz for student {student_id} on topic: {topic}")
    
    try:
        llm = get_llm()
        prompt = PromptTemplate(
            template=QUIZ_PROMPT_TEMPLATE,
            input_variables=["topic", "difficulty", "count"],
            partial_variables={"format_instructions": quiz_output_parser.get_format_instructions()}
        )
        
        chain = prompt | llm | quiz_output_parser
        
        # Use to_thread to avoid 'object can't be awaited' error with Gemini REST transport
        response = await asyncio.to_thread(chain.invoke, {
            "topic": topic,
            "difficulty": difficulty,
            "count": count
        })
        
        questions = normalize_quiz_questions(response.get("questions", []))
        
        # Prepare for database
        quiz_session = {
            "studentId": student_id,
            "courseId": course_id,
            "topic": topic,
            "difficulty": difficulty,
            "questions": questions,
            "generatedAt": datetime.utcnow().isoformat(),
            "status": "active"
        }
        if tenant_id:
            quiz_session["tenantId"] = tenant_id
        if lesson_id:
            quiz_session["lessonId"] = lesson_id
        
        # Save to MongoDB
        result = await ai_quiz_sessions_collection.insert_one(quiz_session)
        quiz_session["id"] = str(result.inserted_id)
        
        return quiz_session
        
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ["429", "resource_exhausted", "quota", "rate limit"]):
            logger.warning("Gemini rate limit hit in quiz generator: %s", str(e))
            raise RateLimitError(
                "Gemini API quota exceeded (429). Please retry in 60 seconds."
            ) from e
            
        logger.error(f"Error generating AI quiz: {str(e)}")
        raise e
