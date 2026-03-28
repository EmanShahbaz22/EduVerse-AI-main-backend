import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routers.roles import admins, students, super_admin, teachers
from app.core.settings import get_cors_origins

from app.routers import (
    adaptive_learning,
    assignment_submissions,
    assignments,
    courses,
    payments,
    quiz_submissions,
    quizzes,
    student_performance,
    student_progress,
    subscription,
    subscription_plans,
    tenants,
    uploads,
)
from app.routers.auth import admin_auth, student_auth, teacher_auth, login
from app.routers.dashboards import admin_dashboard

app = FastAPI(
    title="EduVerse AI Backend",
    description="Multi-Tenant E-Learning Platform API",
    version="1.0.0",
)
logger = logging.getLogger(__name__)


def _format_validation_error(exc: RequestValidationError) -> tuple[str, list[str]]:
    items: list[str] = []
    for err in exc.errors():
        raw_loc = err.get("loc", [])
        loc = ".".join(str(x) for x in raw_loc if str(x) not in {"body", "query", "path"})
        msg = err.get("msg", "Invalid value")
        items.append(f"{loc}: {msg}" if loc else msg)
    if not items:
        return "Invalid request payload.", []
    return "Please fix the highlighted fields and try again.", items


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    message, errors = _format_validation_error(exc)
    return JSONResponse(
        status_code=422,
        content={
            "detail": message,
            "errors": errors,
        },
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong on our side. Please try again.",
        },
    )
@app.get("/")
def root():
    return {
        "message": "EduVerse AI Backend API",
        "version": "1.0.0",
        "status": "operational",
    }


# Include routers

app.include_router(admin_auth.router)
app.include_router(student_auth.router)
app.include_router(teacher_auth.router)
app.include_router(admin_dashboard.router)

app.include_router(login.router)

app.include_router(super_admin.router)
app.include_router(admins.router)
app.include_router(students.router)
app.include_router(teachers.router)

# Student Performance
app.include_router(student_performance.router)

# Student Progress
app.include_router(student_progress.router)

# Course Management
app.include_router(courses.router)
app.include_router(assignments.router)
app.include_router(assignment_submissions.router)


# Tenant & Quizzes
app.include_router(tenants.router)
app.include_router(quizzes.router)
app.include_router(quiz_submissions.router)

# Subscription
app.include_router(subscription.router)
app.include_router(subscription_plans.router)

# Payments (Stripe)
app.include_router(payments.router)

# File uploads
app.include_router(uploads.router)

# Adaptive Learning (AI)
app.include_router(adaptive_learning.router)
