"""
Test Student Classifier — Making Sure Our "Report Card Reader" Works! 🧪

WHY THIS FILE EXISTS:
    We need to be 100% sure the classifier gives the right answer.
    If a student scores 30%, they MUST be classified as "slow".
    If a student scores 90%, they MUST be classified as "fast".

    These tests run automatically in the CI/CD pipeline (Week 4).
    If any test fails, the code won't be deployed — protecting us from bugs!

HOW TO RUN:
    From the backend root:
    .\venv\Scripts\python.exe -m pytest tests/test_classifier.py -v
"""

import sys
import os

# Add the project root to Python path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.student_classifier import classify_student, get_pace_description


# ──────────────────────────────────────────────
# Test 1: Basic score-only classification
# ──────────────────────────────────────────────

def test_slow_student():
    """Score below 40% should be classified as 'slow'."""
    result = classify_student(30.0)
    assert result["pace"] == "slow"
    assert result["score"] == 30.0
    assert "low_score" in result["factors"]


def test_average_student():
    """Score between 40-75% should be classified as 'average'."""
    result = classify_student(55.0)
    assert result["pace"] == "average"
    assert result["score"] == 55.0
    assert "moderate_score" in result["factors"]


def test_fast_student():
    """Score above 75% should be classified as 'fast'."""
    result = classify_student(90.0)
    assert result["pace"] == "fast"
    assert result["score"] == 90.0
    assert "high_score" in result["factors"]


# ──────────────────────────────────────────────
# Test 2: Edge cases (boundary values)
# ──────────────────────────────────────────────

def test_zero_score():
    """0% score should be 'slow'."""
    result = classify_student(0.0)
    assert result["pace"] == "slow"


def test_perfect_score():
    """100% score should be 'fast'."""
    result = classify_student(100.0)
    assert result["pace"] == "fast"


def test_boundary_40_percent():
    """Exactly 40% should be 'average' (40% is the lower boundary of average)."""
    result = classify_student(40.0)
    assert result["pace"] == "average"


def test_boundary_75_percent():
    """Exactly 75% should be 'average' (75% is the upper boundary of average)."""
    result = classify_student(75.0)
    assert result["pace"] == "average"


def test_just_below_40():
    """39.9% should still be 'slow'."""
    result = classify_student(39.9)
    assert result["pace"] == "slow"


def test_just_above_75():
    """75.1% should be 'fast'."""
    result = classify_student(75.1)
    assert result["pace"] == "fast"


# ──────────────────────────────────────────────
# Test 3: Time factor (optional enhancement)
# ──────────────────────────────────────────────

def test_slow_with_high_time_usage():
    """Student who scores low AND uses 90%+ of time → definitely slow."""
    result = classify_student(
        score_percentage=35.0,
        time_spent_seconds=900,   # 15 minutes
        time_limit_seconds=1000,  # ~16.6 minutes limit
    )
    assert result["pace"] == "slow"
    assert "high_time_usage" in result["factors"]


def test_average_downgrade_to_slow():
    """Average score but used almost all time + low score → downgrade to slow."""
    result = classify_student(
        score_percentage=45.0,
        time_spent_seconds=950,
        time_limit_seconds=1000,
    )
    assert result["pace"] == "slow"  # Downgraded from average


def test_average_upgrade_to_fast():
    """Average score but finished quickly + decent score → upgrade to fast."""
    result = classify_student(
        score_percentage=70.0,
        time_spent_seconds=400,
        time_limit_seconds=1000,
    )
    assert result["pace"] == "fast"  # Upgraded from average


def test_time_factor_ignored_when_not_provided():
    """Without time data, classification is score-only."""
    result = classify_student(50.0)
    assert result["pace"] == "average"
    assert len(result["factors"]) == 1  # Only score factor


# ──────────────────────────────────────────────
# Test 4: Return structure
# ──────────────────────────────────────────────

def test_result_has_required_keys():
    """Make sure the result dict has all expected keys."""
    result = classify_student(60.0)
    assert "pace" in result
    assert "score" in result
    assert "factors" in result
    assert "classified_at" in result


# ──────────────────────────────────────────────
# Test 5: Pace descriptions
# ──────────────────────────────────────────────

def test_pace_descriptions():
    """Each pace should have a human-friendly description."""
    assert "reinforcement" in get_pace_description("slow").lower()
    assert "on track" in get_pace_description("average").lower()
    assert "advanced" in get_pace_description("fast").lower()
    assert "unknown" in get_pace_description("invalid_pace").lower()
