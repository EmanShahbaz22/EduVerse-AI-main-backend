from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, require_role
from app.crud.quiz_submissions import (
    delete_submission,
    get_by_quiz,
    get_by_student,
    get_quiz_summary,
    get_student_analytics,
    get_submission_by_id,
    get_teacher_dashboard,
    submit_and_grade_submission,
)
from app.db.database import db
from app.schemas.quiz_submissions import QuizSubmissionCreate, QuizSubmissionResponse

router = APIRouter(prefix="/quiz-submissions", tags=["Quiz Submissions"])


def validate(_id: str, field_name: str = "ObjectId"):
    if not ObjectId.is_valid(_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}",
        )


def _sort_option(sort: Optional[str]):
    if not sort:
        return None
    return (sort.lstrip("-"), -1 if sort.startswith("-") else 1)


async def _get_current_student(current_user: dict):
    student_id = current_user.get("student_id")
    if student_id and ObjectId.is_valid(student_id):
        student = await db.students.find_one({"_id": ObjectId(student_id)})
    else:
        student = await db.students.find_one({"userId": ObjectId(current_user["user_id"])})
    if not student:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student profile not found for current user",
        )
    return student


async def _ensure_student_in_tenant(student_id: str, tenant_id: str):
    student = await db.students.find_one(
        {"_id": ObjectId(student_id), "tenantId": ObjectId(tenant_id)}
    )
    if not student:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: student belongs to a different tenant",
        )


@router.post(
    "/", response_model=QuizSubmissionResponse, summary="Submit answers and auto-grade"
)
async def submit_and_grade_route(
    data: QuizSubmissionCreate,
    current_user=Depends(require_role("student")),
):
    validate(data.quizId, "quizId")
    validate(data.courseId, "courseId")

    student = await _get_current_student(current_user)
    quiz = await db.quizzes.find_one({"_id": ObjectId(data.quizId), "isDeleted": {"$ne": True}})
    if not quiz:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found.",
        )
    if str(quiz.get("courseId")) != data.courseId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quiz does not belong to the selected course.",
        )

    tenant_val = quiz.get("tenantId")
    if not tenant_val:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Quiz tenant context missing",
        )
    tenant_id = str(tenant_val)

    result = await submit_and_grade_submission(
        data, student_id=str(student["_id"]), tenant_id=tenant_id
    )
    if result == "AlreadySubmitted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Student already submitted this quiz.",
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found for current tenant.",
        )
    return result


@router.get(
    "/quiz/{quiz_id}",
    response_model=list[QuizSubmissionResponse],
    summary="Get quiz submissions",
)
async def get_quiz_submissions(
    quiz_id: str,
    sort: Optional[str] = Query(
        None, description="Sort field: submittedAt or -submittedAt"
    ),
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    validate(quiz_id, "quiz_id")
    tenant_id = None if current_user["role"] == "super_admin" else current_user.get(
        "tenant_id"
    )
    return await get_by_quiz(quiz_id, _sort_option(sort), tenant_id=tenant_id)


@router.get(
    "/student/{student_id}",
    response_model=list[QuizSubmissionResponse],
    summary="Get student's submissions",
)
async def get_student_submissions(
    student_id: str,
    sort: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    validate(student_id, "student_id")
    role = current_user["role"]
    tenant_id = None

    if role == "student":
        student = await _get_current_student(current_user)
        if str(student["_id"]) != student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot access another student's submissions",
            )
        tenant_id = None
    elif role in {"admin", "teacher"}:
        tenant_id = current_user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant context required",
            )
        await _ensure_student_in_tenant(student_id, tenant_id)
    elif role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient role",
        )

    return await get_by_student(student_id, _sort_option(sort), tenant_id=tenant_id)


@router.delete("/{_id}", summary="Delete a submission")
async def delete_quiz(_id: str, current_user=Depends(get_current_user)):
    validate(_id, "submission id")

    submission = await get_submission_by_id(_id)
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )

    role = current_user["role"]
    submission_student_id = str(submission["studentId"])
    submission_tenant_id = (
        str(submission["tenantId"]) if submission.get("tenantId") else None
    )

    if role == "student":
        student = await _get_current_student(current_user)
        if str(student["_id"]) != submission_student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot delete another student's submission",
            )
        tenant_scope = submission_tenant_id
    elif role in {"admin", "teacher"}:
        if not submission_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: submission has no tenant scope",
            )
        if current_user.get("tenant_id") != submission_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: tenant mismatch",
            )
        tenant_scope = submission_tenant_id
    elif role == "super_admin":
        tenant_scope = None
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient role",
        )

    deleted = await delete_submission(_id, tenant_id=tenant_scope)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )
    return {"message": "Submission deleted successfully"}


@router.get("/summary/quiz/{quiz_id}", summary="Get aggregated quiz summary")
async def quiz_summary(
    quiz_id: str,
    top_n: int = Query(5, ge=1, le=50),
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    validate(quiz_id, "quiz_id")
    return await get_quiz_summary(quiz_id, top_n=top_n)


@router.get("/analytics/student/{student_id}", summary="Get student analytics")
async def student_analytics(
    student_id: str,
    recent: int = Query(5, ge=1, le=50),
    current_user=Depends(get_current_user),
):
    validate(student_id, "student_id")
    role = current_user["role"]

    if role == "student":
        student = await _get_current_student(current_user)
        if str(student["_id"]) != student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot access another student's analytics",
            )
    elif role in {"admin", "teacher"}:
        tenant_id = current_user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant context required",
            )
        await _ensure_student_in_tenant(student_id, tenant_id)
    elif role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient role",
        )

    return await get_student_analytics(student_id, recent=recent)


@router.get("/dashboard/teacher/{teacher_id}", summary="Get teacher dashboard")
async def teacher_dashboard(
    teacher_id: str,
    course_id: Optional[str] = None,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    validate(teacher_id, "teacher_id")
    if course_id:
        validate(course_id, "course_id")

    if current_user["role"] == "teacher" and current_user.get("teacher_id") != teacher_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: cannot access another teacher dashboard",
        )

    if current_user["role"] in {"admin", "teacher"}:
        teacher = await db.teachers.find_one({"_id": ObjectId(teacher_id)})
        if not teacher or str(teacher.get("tenantId")) != current_user.get("tenant_id"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: tenant mismatch",
            )

    return await get_teacher_dashboard(teacher_id, course_id)
