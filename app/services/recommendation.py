"""
recommendation.py — Rule-based course recommendation engine.

Scores marketplace courses for a student based on:
  - Exact category match with enrolled courses     (+30)
  - Related category match (same domain cluster)   (+20)
  - Level progression from completed level         (+25)
  - Same level as current courses                  (+10)
  - Popularity (enrolledStudents / 10)             (max +15)
  - Free course bonus                              (+5)

No AI/LLM calls. No new dependencies. Runs in <50ms.
"""

import logging
from bson import ObjectId
from app.db.database import db

logger = logging.getLogger(__name__)

# ── Tunable weights ──
W_CATEGORY = 30
W_RELATED_CATEGORY = 20      # same domain cluster, different category name
W_NEXT_LEVEL = 25
W_SAME_LEVEL = 10
W_POPULARITY_MAX = 15
W_FREE = 5

LEVEL_ORDER = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
LEVEL_NEXT = {"Beginner": "Intermediate", "Intermediate": "Advanced", "Advanced": "Advanced"}

# ── Domain clusters: categories in the same cluster are "related" ──
# If a student's enrolled course belongs to any category in a cluster,
# other categories in the same cluster get a W_RELATED_CATEGORY bonus.
DOMAIN_CLUSTERS = [
    # Computing / Technology cluster
    {
        "networking", "network", "computer networks", "computer science",
        "information technology", "it", "cybersecurity", "security",
        "cloud computing", "cloud", "devops", "system administration",
        "operating systems", "databases", "data engineering",
        "software engineering", "software development", "programming",
        "web development", "mobile development", "app development",
        "artificial intelligence", "ai", "machine learning", "ml",
        "deep learning", "data science", "data analysis", "big data",
        "computer engineering", "electronics", "embedded systems",
        "technology", "tech", "stem", "computer",
    },
    # Business / Management cluster
    {
        "business", "management", "entrepreneurship", "marketing",
        "finance", "accounting", "economics", "e-commerce",
        "project management", "leadership", "hr", "human resources",
        "mba", "business administration",
    },
    # Languages cluster
    {
        "english", "urdu", "arabic", "french", "spanish", "german",
        "language", "linguistics", "communication", "writing",
        "literature", "grammar",
    },
    # Design / Creative cluster
    {
        "design", "graphic design", "ui/ux", "ux", "ui", "visual design",
        "photography", "videography", "animation", "3d modeling",
        "art", "creative", "media",
    },
    # Health / Sciences cluster
    {
        "health", "medicine", "biology", "chemistry", "physics",
        "mathematics", "math", "science", "nursing", "pharmacy",
        "psychology", "sociology",
    },
]


def _normalise(cat: str) -> str:
    """Lowercase + strip for robust category comparison."""
    return (cat or "").strip().lower()


def _find_cluster(category: str) -> set | None:
    """Return the domain cluster set that contains the given category, or None."""
    norm = _normalise(category)
    for cluster in DOMAIN_CLUSTERS:
        if norm in cluster:
            return cluster
    return None


def _build_related_categories(enrolled_categories: set[str]) -> set[str]:
    """
    Given the student's enrolled categories, return the full set of related
    categories from the same domain clusters (excluding the enrolled ones).
    """
    related: set[str] = set()
    for cat in enrolled_categories:
        cluster = _find_cluster(cat)
        if cluster:
            related.update(cluster)
    # Remove the enrolled categories themselves (they already get W_CATEGORY)
    enrolled_norm = {_normalise(c) for c in enrolled_categories}
    return {r for r in related if r not in enrolled_norm}


def _highest_level(levels: set[str]) -> str | None:
    """Return the highest level from a set of level strings."""
    best, best_rank = None, -1
    for lvl in levels:
        rank = LEVEL_ORDER.get(lvl, -1)
        if rank > best_rank:
            best, best_rank = lvl, rank
    return best


def _score_course(
    course: dict,
    enrolled_categories_norm: set[str],
    related_categories_norm: set[str],
    next_level: str | None,
    current_level: str | None,
) -> int:
    """Compute a recommendation score for a single course."""
    score = 0
    cat_norm = _normalise(course.get("category", ""))

    if cat_norm in enrolled_categories_norm:
        score += W_CATEGORY
    elif cat_norm in related_categories_norm:
        score += W_RELATED_CATEGORY

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
      3. Query all published courses (not enrolled) — isPublic is NOT required
         so recommendations work regardless of how courses are flagged.
      4. Score each course (category/domain match + level), sort descending
      5. Return top `limit`

    Cold-start fallback: if the student has 0 enrolled courses,
    returns the most popular courses (sorted by enrolledStudents).
    """
    if not ObjectId.is_valid(student_id):
        logger.warning("[Recommend] Invalid student_id: %s", student_id)
        return []
    student = await db.students.find_one({"_id": ObjectId(student_id)})
    if not student:
        logger.warning("[Recommend] Student not found: %s", student_id)
        return []

    raw_enrolled = student.get("enrolledCourses") or []
    logger.info("[Recommend] student=%s raw_enrolledCourses=%s", student_id, raw_enrolled)

    # enrolledCourses may be stored as strings or ObjectIds — handle both
    enrolled_ids = []
    for cid in raw_enrolled:
        try:
            enrolled_ids.append(ObjectId(cid) if not isinstance(cid, ObjectId) else cid)
        except Exception:
            logger.warning("[Recommend] Cannot convert enrolledCourse id to ObjectId: %s", cid)

    logger.info("[Recommend] enrolled_ids count=%d", len(enrolled_ids))

    # ── Build student profile from enrolled courses ──
    enrolled_categories: set[str] = set()
    enrolled_levels: set[str] = set()

    if enrolled_ids:
        enrolled_courses = await db.courses.find({"_id": {"$in": enrolled_ids}}).to_list(length=200)
        logger.info("[Recommend] Fetched %d enrolled course docs", len(enrolled_courses))
        for c in enrolled_courses:
            cat = c.get("category")
            lvl = c.get("level")
            logger.info("[Recommend]   enrolled course '%s' category='%s' level='%s'", c.get("title"), cat, lvl)
            if cat:
                enrolled_categories.add(_normalise(cat))
            if lvl:
                enrolled_levels.add(lvl)
    else:
        logger.info("[Recommend] No enrolled courses found — will use cold-start (most popular)")

    logger.info("[Recommend] enrolled_categories=%s enrolled_levels=%s", enrolled_categories, enrolled_levels)

    current_level = _highest_level(enrolled_levels)
    next_level = LEVEL_NEXT.get(current_level) if current_level else "Beginner"
    related_categories = _build_related_categories(enrolled_categories)

    logger.info(
        "[Recommend] current_level=%s next_level=%s related_categories=%s",
        current_level, next_level, related_categories,
    )

    # ── Fetch all published courses NOT already enrolled in ──
    # NOTE: We intentionally do NOT filter by isPublic here so that
    # recommendations work even if courses are missing that flag.
    query: dict = {"status": {"$regex": "^published$", "$options": "i"}}
    if enrolled_ids:
        query["_id"] = {"$nin": enrolled_ids}

    candidates = await db.courses.find(query).to_list(length=500)
    logger.info("[Recommend] candidate courses found=%d (published, not enrolled)", len(candidates))

    # ── Score and sort ──
    if not enrolled_ids or not enrolled_categories:
        # Cold start: no meaningful profile → sort by popularity
        logger.info("[Recommend] Cold-start path: sorting by enrolledStudents")
        candidates.sort(key=lambda c: c.get("enrolledStudents", 0), reverse=True)
    else:
        for c in candidates:
            c["_score"] = _score_course(
                c,
                enrolled_categories,
                related_categories,
                next_level,
                current_level,
            )
            logger.debug(
                "[Recommend]   course '%s' cat='%s' score=%s",
                c.get("title"), c.get("category"), c["_score"],
            )
        candidates.sort(key=lambda c: c.get("_score", 0), reverse=True)
        top = candidates[:limit]
        logger.info(
            "[Recommend] Top %d after scoring: %s",
            len(top),
            [(c.get("title"), c.get("_score")) for c in top],
        )

    # ── Serialize and return ──
    results = []
    for c in candidates[:limit]:
        c["_id"] = str(c["_id"])
        c["teacherId"] = str(c.get("teacherId", ""))
        c["tenantId"] = str(c.get("tenantId", ""))
        c.pop("_score", None)
        results.append(c)
    return results
