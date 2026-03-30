from fastapi import APIRouter, Depends, HTTPException
from typing import List
from bson import ObjectId
from app.auth.dependencies import require_role, require_tenant
from app.schemas.assignment_submissions import (
    AssignmentSubmissionCreate,
    AssignmentSubmissionUpdate,
    AssignmentSubmissionResponse,
)
from app.crud.assignment_submissions import (
    create_submission,
    get_all_submissions,
    get_submissions_by_student,
    get_submissions_by_assignment,
    grade_submission,
    delete_submission,
)

router = APIRouter(
    prefix="/assignment-submissions",
    tags=["Assignment Submissions"],
)


def validate_object_id(id: str, name: str = "id"):
    if not ObjectId.is_valid(id):
        raise HTTPException(
            status_code=400, detail=f"Invalid ObjectId format for {name}"
        )


def clean_updates(data: dict):
    """Remove empty strings or null-like values from updates."""
    return {k: v for k, v in data.items() if v not in [None, "", [], {}]}


# ===============================
# STUDENT: CREATE SUBMISSION
# ===============================
@router.post("/", response_model=AssignmentSubmissionResponse)
async def create_submission_route(
    data: AssignmentSubmissionCreate,
    current_user=Depends(require_role("student"))
):
    # Enforce tenant for non-students
    if current_user["role"] != "student" and not current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Tenant context required")

    if not data.assignmentId or not data.courseId or not data.fileUrl:
        raise HTTPException(
            status_code=400, detail="assignmentId, courseId, and fileUrl are required"
        )

    validate_object_id(data.assignmentId, "assignmentId")
    validate_object_id(data.courseId, "courseId")

    submission = await create_submission(
        data=data,
        student_id=current_user["user_id"],
        tenant_id=current_user["tenant_id"],
    )

    if not submission:
        raise HTTPException(status_code=500, detail="Failed to create submission")

    return submission


# ===============================
# ADMIN / TEACHER: ALL SUBMISSIONS
# ===============================
@router.get("/", response_model=List[AssignmentSubmissionResponse])
async def get_all_submissions_route(
    current_user=Depends(require_role("admin", "teacher")),
    _=Depends(require_tenant),
):
    submissions = await get_all_submissions(current_user["tenant_id"])
    if submissions is None:
        return []
    return submissions


# ===============================
# STUDENT / TEACHER: BY STUDENT
# ===============================
@router.get("/me", response_model=List[AssignmentSubmissionResponse])
async def get_my_submissions(
    current_user=Depends(require_role("student"))
):
    # Enforce tenant for non-students
    if current_user["role"] != "student" and not current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Tenant context required")
    submissions = await get_submissions_by_student(
        student_id=current_user["user_id"],
        tenant_id=current_user["tenant_id"],
    )
    if submissions is None:
        return []
    return submissions


# ===============================
# TEACHER / ADMIN: BY ASSIGNMENT
# ===============================
@router.get(
    "/assignment/{assignment_id}",
    response_model=List[AssignmentSubmissionResponse],
)
async def get_by_assignment(
    assignment_id: str,
    current_user=Depends(require_role("teacher", "admin")),
    _=Depends(require_tenant),
):
    validate_object_id(assignment_id, "assignmentId")

    submissions = await get_submissions_by_assignment(
        assignment_id=assignment_id,
        tenant_id=current_user["tenant_id"],
    )
    if submissions is None:
        return []
    return submissions


# ===============================
# TEACHER / ADMIN: GRADE
# ===============================
@router.put("/{submission_id}", response_model=AssignmentSubmissionResponse)
async def grade_submission_route(
    submission_id: str,
    update: AssignmentSubmissionUpdate,
    current_user=Depends(require_role("teacher", "admin")),
    _=Depends(require_tenant),
):
    validate_object_id(submission_id, "submissionId")

    if not update or (update.obtainedMarks is None and update.feedback is None):
        raise HTTPException(status_code=400, detail="Nothing to update")

    # Clean empty strings
    update_data = clean_updates(update.model_dump(exclude_unset=True))

    submission = await grade_submission(
        submission_id=submission_id,
        tenant_id=current_user["tenant_id"],
        marks=update_data.get("obtainedMarks"),
        feedback=update_data.get("feedback"),
    )

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    return submission


# ===============================
# ADMIN ONLY: DELETE
# ===============================
@router.delete("/{submission_id}")
async def delete_submission_route(
    submission_id: str,
    current_user=Depends(require_role("admin")),
    _=Depends(require_tenant),
):
    validate_object_id(submission_id, "submissionId")

    success = await delete_submission(
        submission_id=submission_id,
        tenant_id=current_user["tenant_id"],
    )
    if not success:
        raise HTTPException(status_code=404, detail="Submission not found")

    return {"message": "Submission deleted successfully"}
