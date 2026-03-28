"""
student_classifier.py — The "Report Card Reader" 📊

WHY THIS FILE EXISTS:
    After a student takes a quiz, we need to know: are they struggling,
    doing okay, or acing it? This file looks at their score and decides.

    Think of it like a teacher looking at a test and saying:
    - "This student needs extra help" → slow
    - "This student is doing fine" → average
    - "This student is ready for harder stuff" → fast

WHAT IT DOES:
    Takes a quiz score (percentage) and returns one of three labels:
    - "slow"    (score < 40%)
    - "average" (score 40% - 75%)
    - "fast"    (score > 75%)

    Week 3 will add: time factor + streak detection (consecutive scores)
"""

import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def classify_student(
    score_percentage: float,
    time_spent_seconds: Optional[float] = None,
    time_limit_seconds: Optional[float] = None,
) -> dict:
    """
    Classify a student based on their quiz performance.

    Args:
        score_percentage: The quiz score as a percentage (0-100)
        time_spent_seconds: How long the student took (optional, for Week 3)
        time_limit_seconds: The quiz time limit (optional, for Week 3)

    Returns:
        dict with:
            - pace: "slow", "average", or "fast"
            - score: the original score
            - factors: what factors contributed to the classification
            - classified_at: timestamp

    EXAMPLE:
        classify_student(35.0)
        # Returns: {"pace": "slow", "score": 35.0, "factors": ["low_score"], ...}

        classify_student(80.0)
        # Returns: {"pace": "fast", "score": 80.0, "factors": ["high_score"], ...}
    """
    factors = []

    # ── Step 1: Score-based classification (the main factor) ──
    if score_percentage < 40:
        pace = "slow"
        factors.append("low_score")
    elif score_percentage <= 75:
        pace = "average"
        factors.append("moderate_score")
    else:
        pace = "fast"
        factors.append("high_score")

    # ── Step 2: Time factor (optional, will be fully used in Week 3) ──
    # If the student used almost all their time AND scored low → definitely slow
    # If the student finished quickly AND scored high → definitely fast
    if time_spent_seconds is not None and time_limit_seconds is not None and time_limit_seconds > 0:
        time_ratio = time_spent_seconds / time_limit_seconds

        if time_ratio >= 0.9:
            # Used 90%+ of time — this indicates struggling
            factors.append("high_time_usage")
            if pace == "average" and score_percentage < 50:
                pace = "slow"  # Downgrade: struggled AND used lots of time
        elif time_ratio <= 0.5:
            # Finished in under half the time — indicates finding it easy
            factors.append("quick_completion")
            if pace == "average" and score_percentage > 65:
                pace = "fast"  # Upgrade: decent score AND finished quickly

    result = {
        "pace": pace,
        "score": round(float(score_percentage), 2),
        "factors": factors,
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "Classified student: score=%.1f%% → pace=%s (factors: %s)",
        score_percentage, pace, ", ".join(factors)
    )

    return result


def get_pace_description(pace: str) -> str:
    """
    Returns a human-friendly description of what each pace means.
    Useful for showing in the frontend.
    """
    descriptions = {
        "slow": "Needs reinforcement — simpler explanations with more examples and step-by-step guidance.",
        "average": "On track — clear explanations with moderate practice and some challenges.",
        "fast": "Ready for more — concise explanations with advanced concepts and harder exercises.",
    }
    return descriptions.get(pace, "Unknown pace level.")
