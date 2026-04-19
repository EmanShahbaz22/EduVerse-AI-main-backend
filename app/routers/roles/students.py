from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.students import (
    StudentCreate,
    StudentUpdate,
    StudentResponse,
)
from app.crud import students as crud_student
from app.auth.dependencies import get_current_user, require_role
from app.schemas.teachers import ChangePassword


router = APIRouter(prefix="/students", tags=["Students"])


def _enforce_tenant_scope(current_user: dict, tenant_id: str):
    if current_user["role"] == "super_admin":
        return
    if current_user.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )

# PROFILE (ME)


@router.get("/me", response_model=StudentResponse)
async def me(current_user=Depends(require_role("student"))):
    return await crud_student.get_student_me(current_user)


@router.patch("/me", response_model=StudentResponse)
async def update_me(
    payload: StudentUpdate,
    current_user=Depends(require_role("student")),
):
    return await crud_student.update_student_me(current_user, payload)


@router.put("/me/password")
async def change_password(
    payload: ChangePassword,
    current_user=Depends(require_role("student")),
):
    await crud_student.change_student_me_password(
        current_user, payload.oldPassword, payload.newPassword
    )



# -----------------------------------------------------
# LIST STUDENTS FOR TENANT
# -----------------------------------------------------
@router.get(
    "/{tenantId}",
    response_model=list[StudentResponse],
)
async def list_students(
    tenantId: str,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    _enforce_tenant_scope(current_user, tenantId)
    students = await crud_student.list_students(tenantId)

    result = []
    for s in students:
        s["id"] = s["_id"]
        del s["_id"]
        result.append(StudentResponse(**s))

    return result


# -----------------------------------------------------
# GET SINGLE STUDENT
# -----------------------------------------------------
@router.get(
    "/{tenantId}/{studentId}",
    response_model=StudentResponse,
)
async def get_student(
    tenantId: str,
    studentId: str,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    _enforce_tenant_scope(current_user, tenantId)
    student = await crud_student.get_student_by_id(studentId, tenantId)

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    student["id"] = student["_id"]
    del student["_id"]

    return StudentResponse(**student)


# -----------------------------------------------------
# DELETE STUDENT
# -----------------------------------------------------
@router.delete("/{tenantId}/{studentId}")
async def delete_student(
    tenantId: str,
    studentId: str,
    current_user=Depends(require_role("admin", "super_admin")),
):
    _enforce_tenant_scope(current_user, tenantId)
    success = await crud_student.delete_student(studentId, tenantId)

    if not success:
        raise HTTPException(status_code=404, detail="Student not found")

    return {"status": "success", "message": "Student deleted"}
