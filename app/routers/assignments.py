from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId

from app.auth.dependencies import require_role, require_tenant
from app.schemas.assignments import (
    AssignmentCreate,
    AssignmentListResponse,
    AssignmentUpdate,
    AssignmentResponse,
)
from app.crud.assignments import (
    create_assignment,
    get_all_assignments,
    get_assignment,
    update_assignment,
    delete_assignment,
)

router = APIRouter(prefix="/assignments", tags=["Assignments"])


def validate_object_id(id: str, name: str = "id"):
    if not ObjectId.is_valid(id):
        raise HTTPException(
            status_code=400, detail=f"Invalid ObjectId format for {name}"
        )


@router.post("/", response_model=AssignmentResponse)
async def create_assignment_route(
    data: AssignmentCreate,
    current_user=Depends(require_role("teacher")),
    _=Depends(require_tenant),  # Enforce tenant for creation
):
    validate_object_id(data.courseId, "courseId")

    return await create_assignment(
        data,
        teacher_id=current_user["user_id"],
        tenant_id=current_user["tenant_id"],
    )


@router.get("/", response_model=AssignmentListResponse)
async def get_all_assignments_route(
    search: str = None,
    courseId: str = None,
    status: str = None,
    fromDate: datetime = None,
    toDate: datetime = None,
    sortBy: str = "uploadedAt",
    order: int = -1,
    page: int = 1,
    limit: int = 10,
    current_user=Depends(require_role("teacher", "admin", "student"))
):
    # Enforce tenant for non-students
    if current_user["role"] != "student" and not current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Tenant context required")

    if courseId:
        validate_object_id(courseId, "courseId")

    return await get_all_assignments(
        search=search,
        tenant_id=current_user["tenant_id"],  # from token
        teacher_id=(
            current_user["user_id"] if current_user["role"] == "teacher" else None
        ),  # from token (teacher only)
        course_id=courseId,
        status=status,
        from_date=fromDate,
        to_date=toDate,
        sort_by=sortBy,
        order=order,
        page=page,
        limit=limit,
    )


@router.get("/{id}", response_model=AssignmentResponse)
async def get_assignment_route(
    id: str,
    current_user=Depends(require_role("teacher", "admin", "student"))
):
    # Enforce tenant for non-students
    if current_user["role"] != "student" and not current_user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Tenant context required")

    validate_object_id(id, "assignmentId")

    assignment = await get_assignment(
        assignment_id=id,
        tenant_id=current_user["tenant_id"],
    )

    if not assignment:
        raise HTTPException(404, "Assignment not found")

    return assignment


@router.put("/{id}", response_model=AssignmentResponse)
async def update_assignment_route(
    id: str,
    updates: AssignmentUpdate,
    current_user=Depends(require_role("teacher")),
    _=Depends(require_tenant),  # Enforce tenant for update
):
    validate_object_id(id, "assignmentId")

    update_data = updates.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] not in ["active", "inactive"]:
        raise HTTPException(400, "Invalid status value")

    # NEVER allow tenant / teacher reassignment
    update_data.pop("teacherId", None)
    update_data.pop("tenantId", None)

    update_data["updatedAt"] = datetime.utcnow()

    result = await update_assignment(
        assignment_id=id,
        teacher_id=current_user["user_id"],
        tenant_id=current_user["tenant_id"],
        updates=update_data,
    )

    if result == "UNAUTHORIZED":
        raise HTTPException(403, "Not allowed to update this assignment")
    if not result:
        raise HTTPException(404, "Assignment not found")

    return result


@router.delete("/{id}")
async def delete_assignment_route(
    id: str,
    current_user=Depends(require_role("teacher")),
    _=Depends(require_tenant),  # Enforce tenant for deletion
):
    validate_object_id(id, "assignmentId")

    result = await delete_assignment(
        assignment_id=id,
        teacher_id=current_user["user_id"],
        tenant_id=current_user["tenant_id"],
    )

    if result == "UNAUTHORIZED":
        raise HTTPException(403, "Not allowed to delete this assignment")
    if not result:
        raise HTTPException(404, "Assignment not found")

    return {"message": "Assignment deleted successfully"}
