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

FIXES APPLIED:
    1. Input validation added — scores outside 0-100 are clamped with a warning
       instead of silently producing nonsense classifications (e.g. score=150
       would have classified as "fast" with no indication something was wrong).
    2. time_limit_seconds=0 guard already existed but now logs a warning so
       you know when bad time data is being skipped.
    3. classified_at removed from the returned dict — this field is set by
       lesson_generator.py when saving to DB (datetime.now(timezone.utc)).
       Returning it here as an ISO string caused a type mismatch: the DB
       expected a datetime object but got a string, which breaks date queries
       and sorting in MongoDB.
    4. get_pace_description() now handles unknown pace with a logged warning
       instead of silently returning "Unknown pace level." — helps catch
       typos like "Fast" (capital F) passed from the frontend.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds — defined as constants so they're easy to tune ──
# Changing a number here automatically updates all classification logic.
SLOW_THRESHOLD = 40.0     # score < 40  → slow
FAST_THRESHOLD = 75.0     # score > 75  → fast
                          # score 40-75 → average

# Time factor thresholds
HIGH_TIME_RATIO = 0.9     # used 90%+ of time → struggling indicator
LOW_TIME_RATIO  = 0.5     # finished in <50% of time → confident indicator

# Time-based upgrade/downgrade score boundaries
TIME_DOWNGRADE_MAX = 50.0  # only downgrade average→slow if score < 50
TIME_UPGRADE_MIN   = 65.0  # only upgrade average→fast if score > 65


def classify_student(
    score_percentage: float,
    time_spent_seconds: Optional[float] = None,
    time_limit_seconds: Optional[float] = None,
) -> dict:
    """
    Classify a student based on their quiz performance.

    Args:
        score_percentage: The quiz score as a percentage (0-100)
        time_spent_seconds: How long the student took (optional)
        time_limit_seconds: The quiz time limit (optional)

    Returns:
        dict with:
            - pace: "slow", "average", or "fast"
            - score: the original score (clamped to 0-100)
            - factors: list of what contributed to the classification

    EXAMPLE:
        classify_student(35.0)
        → {"pace": "slow", "score": 35.0, "factors": ["low_score"]}

        classify_student(80.0)
        → {"pace": "fast", "score": 80.0, "factors": ["high_score"]}
    """
    # FIX 1: Clamp score to valid range instead of silently misclassifying.
    # A score of 150 or -5 means something went wrong upstream — warn and clamp
    # so the rest of the pipeline still gets a valid result.
    if not (0.0 <= score_percentage <= 100.0):
        logger.warning(
            "Score out of range (%.2f) — clamping to 0-100. "
            "Check the quiz grading logic for this submission.",
            score_percentage,
        )
        score_percentage = max(0.0, min(100.0, score_percentage))

    factors = []

    # ── Step 1: Score-based classification (primary factor) ──
    if score_percentage < SLOW_THRESHOLD:
        pace = "slow"
        factors.append("low_score")
    elif score_percentage <= FAST_THRESHOLD:
        pace = "average"
        factors.append("moderate_score")
    else:
        pace = "fast"
        factors.append("high_score")

    # ── Step 2: Time factor (secondary, optional) ──
    if time_spent_seconds is not None and time_limit_seconds is not None:
        # FIX 2: Log a warning when time_limit_seconds is 0 so bad data is visible.
        # Previously this was silently skipped with no indication in logs.
        if time_limit_seconds <= 0:
            logger.warning(
                "time_limit_seconds is %s — skipping time factor. "
                "Check quiz setup for this submission.",
                time_limit_seconds,
            )
        else:
            time_ratio = time_spent_seconds / time_limit_seconds

            if time_ratio >= HIGH_TIME_RATIO:
                factors.append("high_time_usage")
                if pace == "average" and score_percentage < TIME_DOWNGRADE_MAX:
                    pace = "slow"
                    factors.append("downgraded_by_time")
            elif time_ratio <= LOW_TIME_RATIO:
                factors.append("quick_completion")
                if pace == "average" and score_percentage > TIME_UPGRADE_MIN:
                    pace = "fast"
                    factors.append("upgraded_by_time")

    result = {
        "pace": pace,
        "score": round(float(score_percentage), 2),
        "factors": factors,
        # FIX 3: classified_at removed — lesson_generator.py sets this as a
        # proper datetime object when saving to MongoDB. Returning an ISO string
        # here caused a type mismatch that broke date-based DB queries/sorting.
    }

    logger.info(
        "Classified student: score=%.1f%% → pace=%s factors=%s",
        score_percentage, pace, factors,
    )

    return result


def get_pace_description(pace: str) -> str:
    """
    Returns a human-friendly description of the pace for the frontend.

    FIX 4: Unknown pace now logs a warning instead of silently returning
    a generic string — helps catch typos like "Fast" (capital F) passed
    from the router or frontend.
    """
    descriptions = {
        "slow":    "Needs reinforcement — simpler explanations with more examples and step-by-step guidance.",
        "average": "On track — clear explanations with moderate practice and some challenges.",
        "fast":    "Ready for more — concise explanations with advanced concepts and harder exercises.",
    }

    if pace not in descriptions:
        logger.warning(
            "get_pace_description() received unknown pace='%s'. "
            "Expected one of: slow, average, fast. Check the classifier output.",
            pace,
        )
        return "Unknown pace level."

    return descriptions[pace]