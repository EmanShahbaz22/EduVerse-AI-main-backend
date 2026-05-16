"""
main.py — FastAPI application entry point.

FIXES APPLIED:
    1. Startup event added — calls ping_db() and ensure_indexes() on startup
       so a broken DB or missing indexes are caught immediately, not on the
       first real request. Without this, the server appeared healthy but
       crashed on the first DB call.
    2. Shutdown event added — cleanly closes the MongoDB connection instead
       of leaving it dangling when the server stops.
    3. Logging configured at startup — previously there was no logging setup
       so logger.info/warning/error calls in all other files produced no output.
       Now logs are visible in the console with timestamp + level + message.
    4. Global exception handler now logs the request body on validation errors
       to make debugging 422s easier during development.
    5. HTTPException handler added — previously a raised HTTPException (404,
       503 etc.) would bypass the JSON formatting and return FastAPI's default
       response shape, inconsistent with the validation error format.
"""

import logging
import sys

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers.roles import admins, students, super_admin, teachers
from app.core.settings import get_cors_origins
from app.db.database import ping_db, ensure_indexes   # FIX 1: import startup helpers

from app.routers import (
    adaptive_learning,
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
    ai_tutor,
    reference_upload,   # RAG reference file upload (teacher)
    model_manager,      # Super Admin model control panel
)
from app.routers.auth import admin_auth, student_auth, teacher_auth, login
from app.routers.dashboards import admin_dashboard


# ── FIX 3: Configure logging once at startup ──
# Without this, all logger.info/warning/error calls in every other file
# produce no output — silent failures are very hard to debug.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("app.log")
    ],
)
logger = logging.getLogger(__name__)


async def _seed_active_worker_model() -> None:
    """
    Ensure the active_worker_model config document exists in MongoDB.
    If missing (fresh DB), inserts the default value 'phi3.5'.
    The Super Admin can change this at runtime via /admin/models/set-active.
    """
    from app.db.database import config_collection
    existing = await config_collection.find_one({"_id": "active_worker_model"})
    if not existing:
        await config_collection.insert_one({
            "_id":        "active_worker_model",
            "value":      "phi3.5",
            "updated_by": "system",
        })
        logger.info("Seeded active_worker_model = 'phi3.5' in MongoDB config.")
    else:
        logger.info("active_worker_model already set to '%s'.", existing.get("value"))


# ── FIX 1 & 2: Lifespan — startup + shutdown in one place ──
# Old code had no startup event at all. The server appeared healthy but:
# - DB connection was never verified (broken MONGO_URI discovered on first request)
# - Indexes were never created (full collection scans on every AI query)
# - MongoDB connection was never closed on shutdown (connection leak)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    logger.info("Starting EduVerse AI Backend...")
    try:
        await ping_db()          # verify MongoDB is reachable
        await ensure_indexes()   # create indexes if they don't exist yet
        await _seed_active_worker_model()   # ensure config doc exists in MongoDB
        logger.info("Startup complete — server is ready.")
    except RuntimeError as e:
        # DB is unreachable or misconfigured — log and exit immediately.
        # Continuing with a broken DB just causes every request to fail.
        logger.critical("Startup failed: %s", str(e))
        sys.exit(1)

    yield  # server runs here

    # ── Shutdown ──
    from app.db.database import client
    client.close()
    logger.info("MongoDB connection closed. Server stopped.")


# ── App instance ──
app = FastAPI(
    title="EduVerse AI Backend",
    description="Multi-Tenant E-Learning Platform API",
    version="1.0.0",
    lifespan=lifespan,   # FIX 1+2: wire in the lifespan handler
)


# ── Middleware ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Exception handlers ──

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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    message, errors = _format_validation_error(exc)
    # FIX 4: Log validation failures so you can see what bad data came in
    logger.warning(
        "Validation error on %s %s — %s",
        request.method, request.url.path, errors,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": message,
            "errors": errors,
        },
    )


# FIX 5: HTTPException handler — previously 404/503 etc. returned FastAPI's
# default response shape instead of the consistent JSON format used everywhere.
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTP %s on %s %s — %s",
        exc.status_code, request.method, request.url.path, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled server error on %s %s",
        request.method, request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. Please try again."},
    )


# ── Health check ──
@app.get("/", tags=["Health"])
def root():
    return {
        "message": "EduVerse AI Backend API",
        "version": "1.0.0",
        "status": "operational",
    }


# ── Routers ──

# Auth
app.include_router(admin_auth.router)
app.include_router(student_auth.router)
app.include_router(teacher_auth.router)
app.include_router(login.router)

# Dashboards
app.include_router(admin_dashboard.router)

# Roles
app.include_router(super_admin.router)
app.include_router(admins.router)
app.include_router(students.router)
app.include_router(teachers.router)

# Student
app.include_router(student_performance.router)
app.include_router(student_progress.router)

# Courses
app.include_router(courses.router)

# Tenants & Quizzes
app.include_router(tenants.router)
app.include_router(quizzes.router)
app.include_router(quiz_submissions.router)

# Subscriptions & Payments
app.include_router(subscription.router)
app.include_router(subscription_plans.router)
app.include_router(payments.router)

# Uploads
app.include_router(uploads.router)

# Adaptive Learning (AI)
app.include_router(adaptive_learning.router)
app.include_router(ai_tutor.router)

# RAG Reference Uploads (teacher) + Model Control (Super Admin)
app.include_router(reference_upload.router)
app.include_router(model_manager.router)
app.include_router(model_manager.validations_router)
