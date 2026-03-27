import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
client = AsyncIOMotorClient(MONGO_URI)
db = client["LMS"]

def get_courses_collection():
    return db["courses"]

def get_students_collection():
    return db["students"]

student_performance_collection = db["studentPerformance"]
students_collection = db["students"]
courses_collection = db["courses"]
assignments_collection = db["assignments"]              
assignment_submissions_collection = db["assignmentSubmissions"]
quizzes_collection = db["quizzes"]
quiz_submissions_collection = db["quizSubmissions"]
users_collection = db["users"]
subscription_plans_collection = db["subscriptionPlans"]

# ── AI / Adaptive Learning collections (Member 1) ──
ai_generated_lessons_collection = db["aiGeneratedLessons"]
student_classifications_collection = db["studentClassifications"]


async def ensure_indexes():
    await users_collection.create_index("email", name="users_email_idx")
    await users_collection.create_index(
        [("role", 1), ("tenantId", 1)], name="users_role_tenant_idx"
    )
    await students_collection.create_index(
        [("userId", 1), ("tenantId", 1)], name="students_user_tenant_idx"
    )
    await students_collection.create_index(
        [("tenantId", 1), ("enrolledCourses", 1)],
        name="students_tenant_enrolled_courses_idx",
    )
    await courses_collection.create_index(
        [("tenantId", 1), ("teacherId", 1)], name="courses_tenant_teacher_idx"
    )
    await assignments_collection.create_index(
        [("tenantId", 1), ("courseId", 1)], name="assignments_tenant_course_idx"
    )
    await quiz_submissions_collection.create_index(
        [("quizId", 1), ("studentId", 1)], name="quiz_submissions_quiz_student_idx"
    )
    await subscription_plans_collection.create_index(
        [("code", 1)], name="subscription_plans_code_idx"
    )
    await subscription_plans_collection.create_index(
        [("status", 1)], name="subscription_plans_status_idx"
    )
