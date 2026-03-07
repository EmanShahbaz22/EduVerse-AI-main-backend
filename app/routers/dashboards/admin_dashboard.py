from fastapi import APIRouter, Depends
from app.crud.dashboards import admin_dashboard as crud_admin
from app.auth.dependencies import get_current_user, require_role

router = APIRouter(prefix="/admin/dashboard", tags=["Admin Dashboard"])

# Only admin and super-admin can access these endpoints
admin_roles = ["admin", "super_admin"]


@router.get("/teachers")
async def list_teachers(current_user=Depends(require_role(*admin_roles))):
    teachers = await crud_admin.get_all_teachers(current_user["tenant_id"])
    return {"total": len(teachers), "teachers": teachers}


@router.get("/students")
async def list_students(current_user=Depends(require_role(*admin_roles))):
    students = await crud_admin.get_all_students(current_user["tenant_id"])
    return {"total": len(students), "students": students}


@router.get("/courses")
async def list_courses(current_user=Depends(require_role(*admin_roles))):
    courses = await crud_admin.get_all_courses(current_user["tenant_id"])
    return {"total": len(courses), "courses": courses}
