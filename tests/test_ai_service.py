"""
Test AI Service — Making Sure Our AI Setup Works! 🧪

WHY THIS FILE EXISTS:
    We test the AI service to make sure:
    1. The prompt template is correctly built
    2. The output parser expects the right fields
    3. The generate_lesson function handles errors gracefully

    We DON'T call the real Gemini API in tests (that would be slow + cost money).
    Instead, we "mock" (fake) the AI response to test our parsing logic.

HOW TO RUN:
    From the backend root:
    .\venv\Scripts\python.exe -m pytest tests/test_ai_service.py -v
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ai_service import (
    LESSON_RESPONSE_SCHEMAS,
    lesson_output_parser,
    lesson_prompt,
    GEMINI_API_KEY,
)


# ──────────────────────────────────────────────
# Test 1: Output parser has the right fields
# ──────────────────────────────────────────────

def test_lesson_response_schemas_count():
    """We should have exactly 6 response fields defined."""
    assert len(LESSON_RESPONSE_SCHEMAS) == 6


def test_lesson_response_schema_names():
    """Check that all expected field names are in the schema."""
    schema_names = [s.name for s in LESSON_RESPONSE_SCHEMAS]
    expected = ["title", "content", "difficulty", "estimated_duration_minutes", "key_concepts", "summary"]
    for name in expected:
        assert name in schema_names, f"Missing schema field: {name}"


# ──────────────────────────────────────────────
# Test 2: Prompt template has the right variables
# ──────────────────────────────────────────────

def test_lesson_prompt_input_variables():
    """The prompt should accept pace, topic, score, and weak_areas."""
    assert "pace" in lesson_prompt.input_variables
    assert "topic" in lesson_prompt.input_variables
    assert "score" in lesson_prompt.input_variables
    assert "weak_areas" in lesson_prompt.input_variables


def test_lesson_prompt_has_format_instructions():
    """The prompt should include format_instructions for structured output."""
    assert "format_instructions" in lesson_prompt.partial_variables


# ──────────────────────────────────────────────
# Test 3: Output parser works with valid JSON
# ──────────────────────────────────────────────

def test_output_parser_parses_valid_response():
    """Parser should correctly parse a well-formatted AI response."""
    # Simulate what Gemini would return
    fake_response = """```json
{
    "title": "Understanding Fractions",
    "content": "# Fractions\\nA fraction represents a part of a whole...",
    "difficulty": "beginner",
    "estimated_duration_minutes": "15",
    "key_concepts": ["numerator", "denominator", "simplification"],
    "summary": "This lesson covers the basics of fractions."
}
```"""
    result = lesson_output_parser.parse(fake_response)
    assert result["title"] == "Understanding Fractions"
    assert result["difficulty"] == "beginner"


# ──────────────────────────────────────────────
# Test 4: Prompt renders correctly
# ──────────────────────────────────────────────

def test_prompt_renders_with_variables():
    """The prompt should render with given variables without errors."""
    rendered = lesson_prompt.format(
        pace="slow",
        topic="Algebra",
        score="35",
        weak_areas="factoring, simplification",
    )
    assert "slow" in rendered
    assert "Algebra" in rendered
    assert "35" in rendered
    assert "factoring" in rendered
