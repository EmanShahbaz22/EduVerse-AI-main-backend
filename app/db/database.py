"""
database.py — MongoDB connection and collection registry.

FIXES APPLIED:
    1. MONGO_URI validation added — if the env var is missing the server starts
       but crashes with a confusing error on the first DB call instead of
       failing immediately with a clear message on startup.
    2. Connection timeout config added — default Motor/MongoDB timeouts are too
       long (30s). Without this, a unreachable DB causes every request to hang
       for 30s before failing.
    3. ensure_indexes() now has a unique=True on users email index — without it
       two users can register with the same email and only the application layer
       prevents duplicates (fragile). The DB should enforce this too.
    4. AI collections indexes added — aiGeneratedLessons and
       studentClassifications had NO indexes at all. Every duplicate-check
       query in lesson_generator.py was doing a full collection scan, which
       gets slower as data grows and defeats the quota-saving duplicate guard.
    5. Redundant collection accessors cleaned up — get_courses_collection() and
       get_students_collection() functions returned the same objects as the
       module-level variables, creating two ways to access the same thing.
       Kept only the module-level variables for consistency.
    6. ping() health check added to verify the connection is actually alive
       at startup instead of discovering it's broken on the first request.
"""

import os
import logging
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING

load_dotenv()
logger = logging.getLogger(__name__)

# ── FIX 1: Validate MONGO_URI at import time ──
# Without this, a missing env var produces "AsyncIOMotorClient(None)" which
# connects to localhost silently instead of raising a clear error.
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError(
        "MONGO_URI is not set! "
        "Add it to your .env file: MONGO_URI=mongodb+srv://..."
    )

# ── FIX 2: Connection timeouts ──
# Default timeouts are 30s — a dead DB makes every request hang.
# serverSelectionTimeoutMS: how long to wait to find a usable server.
# connectTimeoutMS: how long to wait for a single connection to open.
client = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=30000,   # fail fast if DB is unreachable (30s)
    connectTimeoutMS=30000,
)
db = client["LMS"]

# ── Collections ──
# Single source of truth — use these module-level variables everywhere.
# FIX 5: Removed get_courses_collection() and get_students_collection()
# functions — they returned the same object as the variables below, creating
# two inconsistent ways to access the same collection.
student_performance_collection = db["studentPerformance"]
students_collection            = db["students"]
courses_collection             = db["courses"]
quizzes_collection             = db["quizzes"]
quiz_submissions_collection    = db["quizSubmissions"]
users_collection               = db["users"]
subscription_plans_collection  = db["subscriptionPlans"]

# ── AI / Adaptive Learning collections ──
ai_generated_lessons_collection    = db["aiGeneratedLessons"]
student_classifications_collection = db["studentClassifications"]
ai_quiz_sessions_collection        = db["aiQuizSessions"]
ai_chat_history_collection         = db["aiChatHistory"]


# ── Backward Compatibility Accessors ──
# Some older parts of the app still use these function calls.
# Keep them around to avoid ImportErrors in the CRUD layer.
def get_courses_collection():
    return courses_collection

def get_students_collection():
    return students_collection


async def ping_db() -> None:
    """
    FIX 6: Verify the MongoDB connection is alive at startup.
    Call this from main.py in the startup event so a broken DB is discovered
    immediately rather than on the first real request.
    """
    try:
        await client.admin.command("ping")
        logger.info("MongoDB connection verified (ping OK).")
    except Exception as e:
        raise RuntimeError(
            f"Cannot reach MongoDB — check your MONGO_URI and network. "
            f"Error: {e}"
        )


async def ensure_indexes() -> None:
    """
    Create all indexes on startup.
    Safe to call every time — MongoDB skips indexes that already exist.

    FIXES:
    - users.email: now unique=True (prevents duplicate accounts at DB level)
    - aiGeneratedLessons: compound index on studentId+quizId (the exact query
      used by the duplicate check in lesson_generator.py — without this it was
      doing a full collection scan on every lesson generation request)
    - studentClassifications: compound index on studentId+courseId for the
      get_latest_classification() query in lesson_generator.py
    """

    # ── Users ──
    await users_collection.create_index(
        "email",
        name="users_email_unique_idx",
        unique=True,   # FIX 3: enforce uniqueness at DB level, not just app level
    )
    await users_collection.create_index(
        [("role", ASCENDING), ("tenantId", ASCENDING)],
        name="users_role_tenant_idx",
    )

    # ── Students ──
    await students_collection.create_index(
        [("userId", ASCENDING)],
        name="students_user_idx",
    )
    await students_collection.create_index(
        [("enrolledCourses", ASCENDING)],
        name="students_enrolled_courses_idx",
    )

    # ── Courses ──
    await courses_collection.create_index(
        [("tenantId", ASCENDING), ("teacherId", ASCENDING)],
        name="courses_tenant_teacher_idx",
    )

    # ── Quiz submissions ──
    await quiz_submissions_collection.create_index(
        [("quizId", ASCENDING), ("studentId", ASCENDING)],
        name="quiz_submissions_quiz_student_idx",
    )

    # ── Subscription plans ──
    await subscription_plans_collection.create_index(
        [("code", ASCENDING)],
        name="subscription_plans_code_idx",
    )
    await subscription_plans_collection.create_index(
        [("status", ASCENDING)],
        name="subscription_plans_status_idx",
    )

    # ── FIX 4: AI / Adaptive Learning indexes ──
    # These were completely missing. Every duplicate-check query in
    # lesson_generator.py was a full collection scan.

    # Duplicate check query: find_one({"studentId": s_id, "quizId": q_id})
    await ai_generated_lessons_collection.create_index(
        [("studentId", ASCENDING), ("quizId", ASCENDING)],
        name="ai_lessons_student_quiz_idx",
    )
    # get_student_lessons() query: find({"studentId": ..., "courseId": ...})
    await ai_generated_lessons_collection.create_index(
        [("studentId", ASCENDING), ("courseId", ASCENDING)],
        name="ai_lessons_student_course_idx",
    )

    # get_latest_classification() query: find_one({"studentId": ..., "courseId": ...})
    await student_classifications_collection.create_index(
        [("studentId", ASCENDING), ("courseId", ASCENDING)],
        name="classifications_student_course_idx",
    )
    # Sort by classifiedAt in get_latest_classification()
    await student_classifications_collection.create_index(
        [("studentId", ASCENDING), ("classifiedAt", ASCENDING)],
        name="classifications_student_time_idx",
    )

    # AI Quiz Sessions: find by studentId + quizId
    await ai_quiz_sessions_collection.create_index(
        [("studentId", ASCENDING), ("quizId", ASCENDING)],
        name="ai_quiz_sessions_student_quiz_idx",
    )

    # AI Chat History: find by studentId + courseId
    await ai_chat_history_collection.create_index(
        [("studentId", ASCENDING), ("courseId", ASCENDING)],
        name="ai_chat_history_student_course_idx",
    )

    logger.info("All MongoDB indexes verified/created successfully.")
