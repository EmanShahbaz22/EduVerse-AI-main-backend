"""
recommendation.py — Rule-based course recommendation engine.

Scores marketplace courses for a student based on:
  - Category match with enrolled courses  (+30)
  - Level progression from completed level (+25)
  - Same level as current courses          (+10)
  - Popularity (enrolledStudents / 10)     (max +15)
  - Free course bonus                      (+5)

No AI/LLM calls. No new dependencies. Runs in <50ms.
"""

from bson import ObjectId
from app.db.database import db

# ── Tunable weights ──
W_CATEGORY = 30
W_NEXT_LEVEL = 25
W_SAME_LEVEL = 10
W_POPULARITY_MAX = 15
W_FREE = 5

LEVEL_ORDER = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
LEVEL_NEXT = {"Beginner": "Intermediate", "Intermediate": "Advanced", "Advanced": "Advanced"}


def _highest_level(levels: set[str]) -> str | None:
    """Return the highest level from a set of level strings."""
    best, best_rank = None, -1
    for lvl in levels:
        rank = LEVEL_ORDER.get(lvl, -1)
        if rank > best_rank:
            best, best_rank = lvl, rank
    return best


def _score_course(course: dict, enrolled_categories: set[str], next_level: str | None, current_level: str | None) -> int:
    """Compute a recommendation score for a single course."""
    score = 0
    if course.get("category") in enrolled_categories:
        score += W_CATEGORY
    lvl = course.get("level")
    if next_level and lvl == next_level:
        score += W_NEXT_LEVEL
    elif current_level and lvl == current_level:
        score += W_SAME_LEVEL
    score += min((course.get("enrolledStudents", 0)) / 10, W_POPULARITY_MAX)
    if course.get("isFree", False):
        score += W_FREE
    return score


async def get_recommended_courses(student_id: str, limit: int = 10) -> list[dict]:
    """
    Return up to `limit` recommended courses for the given student.

    Steps:
      1. Fetch student doc → enrolledCourses list
      2. Fetch enrolled courses → extract categories + highest level
      3. Query marketplace courses (published + public, not enrolled)
      4. Score each course, sort descending
      5. Return top `limit`

    Cold-start fallback: if the student has 0 enrolled courses,
    returns the most popular courses (sorted by enrolledStudents).
    """
    if not ObjectId.is_valid(student_id):
        return []
    student = await db.students.find_one({"_id": ObjectId(student_id)})
    if not student:
        return []

    enrolled_ids = [ObjectId(cid) for cid in (student.get("enrolledCourses") or [])]


    # ── Build student profile from enrolled courses ──
    enrolled_categories: set[str] = set()
    enrolled_levels: set[str] = set()

    if enrolled_ids:
        enrolled_courses = await db.courses.find({"_id": {"$in": enrolled_ids}}).to_list(length=200)
        for c in enrolled_courses:
            if c.get("category"):
                enrolled_categories.add(c["category"])
            if c.get("level"):
                enrolled_levels.add(c["level"])

    current_level = _highest_level(enrolled_levels)
    next_level = LEVEL_NEXT.get(current_level) if current_level else "Beginner"

    # ── Fetch marketplace courses (published + public, not enrolled) ──
    query = {"status": "published", "isPublic": True}
    if enrolled_ids:
        query["_id"] = {"$nin": enrolled_ids}

    candidates = await db.courses.find(query).to_list(length=200)

    # ── Cold start: no enrolled courses → return most popular ──
    if not enrolled_ids:
        candidates.sort(key=lambda c: c.get("enrolledStudents", 0), reverse=True)
    else:
        for c in candidates:
            c["_score"] = _score_course(c, enrolled_categories, next_level, current_level)
        candidates.sort(key=lambda c: c.get("_score", 0), reverse=True)

    # ── Serialize and return ──
    results = []
    for c in candidates[:limit]:
        c["_id"] = str(c["_id"])
        c["teacherId"] = str(c.get("teacherId", ""))
        c["tenantId"] = str(c.get("tenantId", ""))
        c.pop("_score", None)
        results.append(c)
    return results
