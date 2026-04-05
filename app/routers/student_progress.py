from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List
from app.auth.dependencies import require_role
from app.schemas.student_progress import (
    MarkLessonCompleteRequest,
    CourseProgressResponse,
)
from app.crud.student_progress import progress_crud

router = APIRouter(prefix="/courses/progress", tags=["Student Progress"])


@router.get("/{courseId}", response_model=CourseProgressResponse)
async def get_course_progress(
    courseId: str,
    tenantId: str | None = Query(None, alias="tenantId"),
    current_user=Depends(require_role("student")),
):
    """Fetch progress for a specific course."""
    try:
        # Use userId from token
        student_id = current_user.get("user_id")
        progress = await progress_crud.get_or_create_progress(
            student_id, courseId, tenantId
        )
        return progress
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/mark-complete", response_model=CourseProgressResponse)
async def mark_lesson_complete(
    data: MarkLessonCompleteRequest,
    tenantId: str | None = Query(None, alias="tenantId"),
    current_user=Depends(require_role("student")),
):
    """Mark a lesson as complete and return updated progress."""
    try:
        student_id = current_user.get("user_id")
        result = await progress_crud.mark_lesson_complete(
            student_id, data.courseId, tenantId, data.lessonId
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/summary/all", response_model=List[CourseProgressResponse])
async def get_all_progress(
    tenantId: str | None = Query(None, alias="tenantId"),
    current_user=Depends(require_role("student")),
):
    """Get all course progress records for the student."""
    student_id = current_user.get("user_id")
    results = await progress_crud.get_student_course_progress(student_id, tenantId)
    return results
