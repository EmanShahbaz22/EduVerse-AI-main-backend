from fastapi import APIRouter, HTTPException, Query, status, Depends
from bson import ObjectId
from typing import Optional

from app.schemas.quizzes import QuizCreate, QuizUpdate, QuizResponse
from app.schemas.adaptive_learning import AIQuizResponse
from app.db.database import ai_quiz_sessions_collection
from app.services.quiz_generator import normalize_quiz_questions
from app.crud.quizzes import (
    create_quiz,
    get_quiz,
    get_quizzes_filtered,
    update_quiz,
    delete_quiz,
    get_student_quizzes,
    has_quiz_submissions,
)
from app.auth.dependencies import get_current_user, require_role

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])


# ------------------ STUDENT SPECIFIC ------------------
@router.get("/student/me", response_model=list[AIQuizResponse])
async def get_my_quizzes(current_user=Depends(get_current_user)):
    """
    Return AI-generated quiz sessions for the current student.
    Falls back to legacy enrolled-course quizzes if none exist.
    """
    if current_user["role"] != "student":
        raise HTTPException(
            status_code=403, detail="Only students can access this endpoint"
        )

    # Prefer AI quiz sessions (adaptive pipeline output)
    student_id = current_user.get("student_id") or current_user.get("user_id")
    # Only return quizzes with at least 1 question — 0-question records are
    # failed Ollama generation artefacts and should never be shown or polled.
    ai_cursor = ai_quiz_sessions_collection.find(
        {
            "studentId": student_id,
            "questions": {"$exists": True, "$not": {"$size": 0}},
        }
    ).sort("generatedAt", -1)
    ai_quizzes = []
    async for q in ai_cursor:
        ai_quizzes.append(
            AIQuizResponse(
                id=str(q.get("_id")),
                studentId=str(q.get("studentId")),
                courseId=str(q.get("courseId")),
                lessonId=q.get("lessonId"),
                topic=q.get("topic", ""),
                questions=normalize_quiz_questions(q.get("questions", [])),
                generatedAt=q.get("generatedAt"),
            )
        )

    if ai_quizzes:
        return ai_quizzes

    # Legacy quizzes collection (kept for backward compatibility)
    return await get_student_quizzes(
        user_id=current_user["user_id"], tenant_id=current_user.get("tenant_id")
    )


# ------------------ VALIDATION ------------------
def _validate_objectid(_id: str):
    """Ensures that incoming IDs are valid MongoDB ObjectIds."""
    if not ObjectId.is_valid(_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format provided"
        )


# ------------------ CREATE QUIZ ------------------
@router.post("/", response_model=QuizResponse, summary="Create a new quiz")
async def create_quiz_route(
    data: QuizCreate, current_user=Depends(require_role("admin", "teacher"))
):
    # Validate IDs coming from body
    _validate_objectid(data.courseId)
    _validate_objectid(data.teacherId)
    _validate_objectid(data.tenantId)

    # Call CRUD function
    return await create_quiz(data)


# ------------------ GET QUIZ BY ID ------------------
@router.get("/{quiz_id}", response_model=QuizResponse, summary="Get quiz by ID")
async def get_one(quiz_id: str):
    _validate_objectid(quiz_id)
    quiz = await get_quiz(quiz_id)
    if not quiz:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found"
        )
    return quiz


# ------------------ LIST QUIZZES (FILTERING + SEARCH + PAGINATION) ------------------
@router.get(
    "/",
    response_model=list[QuizResponse],
    summary="List quizzes with filtering, searching, sorting, pagination",
)
async def list_quizzes(
    tenant_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    course_id: Optional[str] = None,
    search: Optional[str] = Query(None, description="search in description"),
    sort: Optional[str] = Query(
        "createdAt", description="Sort results: 'name' or 'createdAt or '-createdAt'"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):

    # Validate IDs only if provided
    if tenant_id:
        _validate_objectid(tenant_id)
    if teacher_id:
        _validate_objectid(teacher_id)
    if course_id:
        _validate_objectid(course_id)

    # Forward to CRUD function
    return await get_quizzes_filtered(
        tenant_id, teacher_id, course_id, search, sort, page, limit
    )


# ------------------ UPDATE QUIZ ------------------
@router.patch(
    "/{quiz_id}", response_model=QuizResponse, summary="Update/Patch quiz by ID"
)
async def update_quiz_route(
    quiz_id: str,
    updates: QuizUpdate,
    current_user=Depends(require_role("admin", "teacher")),
):
    _validate_objectid(quiz_id)
    teacher_id = current_user["user_id"]

    result = await update_quiz(
        quiz_id, teacher_id, updates.model_dump(exclude_unset=True)
    )

    if result == "Unauthorized":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to edit this quiz. Only the quiz creator can make changes.",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found"
        )

    return result


# ------------------ CHECK SUBMISSIONS ------------------
@router.get(
    "/{quiz_id}/has-submissions", summary="Check if quiz has student submissions"
)
async def check_quiz_submissions(quiz_id: str):
    """
    Check if a quiz has any student submissions.
    Useful for frontend to warn teachers before editing.
    """
    _validate_objectid(quiz_id)
    has_subs = await has_quiz_submissions(quiz_id)
    return {
        "quizId": quiz_id,
        "hasSubmissions": has_subs,
        "message": "Questions cannot be modified once students have submitted answers."
        if has_subs
        else "Quiz can be fully edited.",
    }


# ------------------ DELETE QUIZ ------------------
@router.delete("/{quiz_id}", summary="Delete quiz by ID")
async def delete_quiz_route(
    quiz_id: str, current_user=Depends(require_role("admin", "teacher"))
):
    _validate_objectid(quiz_id)
    teacher_id = current_user["user_id"]

    result = await delete_quiz(quiz_id, teacher_id)

    if result == "Unauthorized":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to delete this quiz. Only the quiz creator can delete it.",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found"
        )

    return {"message": "Quiz deleted successfully"}
