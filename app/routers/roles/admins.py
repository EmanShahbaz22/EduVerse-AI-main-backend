from fastapi import APIRouter, HTTPException, Depends, status
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
from app.schemas.teachers import TeacherUpdate
from app.crud import admins as crud_admin
from app.crud.students import delete_student as crud_delete_student
from app.db.database import db

from app.crud.teachers import (
    delete_teacher as crud_delete_teacher,
    update_teacher as crud_update_teacher,
)
from app.auth.dependencies import get_current_user, require_role
from app.schemas.admins import AdminResponse, AdminUpdateProfile, AdminUpdatePassword
from app.crud.admins import (
    get_admin_me,
    update_admin_me,
    change_admin_me_password,
)


load_dotenv()

router = APIRouter(
    prefix="/admin",
    tags=["Admin – Self"],
    dependencies=[Depends(require_role("admin"))],
)


def _to_oid(value: str, field_name: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ObjectId for {field_name}",
        )
    return ObjectId(value)


def _tenant_oid(current_user: dict) -> ObjectId:
    tenant_id = current_user.get("tenant_id")
    if not tenant_id or not ObjectId.is_valid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )
    return ObjectId(tenant_id)


@router.get("/me", response_model=AdminResponse)
async def me(current_user=Depends(get_current_user)):
    return await get_admin_me(current_user)


@router.patch("/me", response_model=AdminResponse)
async def update_me(
    payload: AdminUpdateProfile,
    current_user=Depends(get_current_user),
):
    return await update_admin_me(current_user, payload)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: AdminUpdatePassword,
    current_user=Depends(get_current_user),
):
    await change_admin_me_password(
        current_user, payload.oldPassword, payload.newPassword
    )


# ------------------ Dashboard ------------------


@router.get("/teachers")
async def list_teachers(current_user=Depends(get_current_user)):
    teachers = await crud_admin.get_all_teachers(current_user["tenant_id"])
    return {"total": len(teachers), "teachers": teachers}


@router.get("/students")
async def list_students(current_user=Depends(get_current_user)):
    students = await crud_admin.get_all_students(current_user["tenant_id"])
    return {"total": len(students), "students": students}


@router.get("/courses")
async def list_courses(current_user=Depends(get_current_user)):
    courses = await crud_admin.get_all_courses(current_user["tenant_id"])
    return {"total": len(courses), "courses": courses}


# ------------------ Students Endpoints ------------------


@router.patch("/students/{student_id}")
async def update_student(
    student_id: str, data: dict, current_user=Depends(get_current_user)
):
    student_oid = _to_oid(student_id, "student_id")
    tenant_oid = _tenant_oid(current_user)
    student = await db.students.find_one({"_id": student_oid, "tenantId": tenant_oid})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    update_data = {k: v for k, v in data.items() if v is not None}
    user_fields = {
        "fullName",
        "email",
        "profileImageURL",
        "contactNo",
        "country",
        "status",
    }
    student_fields = {"enrolledCourses", "completedCourses"}
    user_updates = {k: v for k, v in update_data.items() if k in user_fields}
    student_updates = {k: v for k, v in update_data.items() if k in student_fields}

    if user_updates:
        if user_updates.get("email"):
            user_updates["email"] = user_updates["email"].lower()
        user_updates["updatedAt"] = datetime.utcnow()
        await db.users.update_one(
            {"_id": student["userId"], "tenantId": tenant_oid}, {"$set": user_updates}
        )
    if student_updates:
        student_updates["updatedAt"] = datetime.utcnow()
        await db.students.update_one({"_id": student_oid}, {"$set": student_updates})

    updated_student = await db.students.find_one({"_id": student_oid, "tenantId": tenant_oid})
    updated_user = await db.users.find_one({"_id": updated_student["userId"]})

    return {
        "id": str(updated_student["_id"]),
        "name": updated_user.get("fullName", ""),
        "email": updated_user.get("email", ""),
        "class": updated_student.get("className", "N/A"),
        "rollNo": updated_student.get("rollNo", "N/A"),
        "status": updated_user.get("status", "active"),
    }


@router.delete("/students/{student_id}")
async def delete_student(student_id: str, current_user=Depends(get_current_user)):
    tenant_id = current_user["tenant_id"]
    # crud_delete_student handles both student and user documents
    success = await crud_delete_student(student_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"message": "Student deleted successfully"}


# ------------------ Teachers Endpoints ------------------


@router.put("/update-teacher/{id}")
async def admin_update_teacher(
    id: str, updates: TeacherUpdate, current_user=Depends(get_current_user)
):
    teacher_oid = _to_oid(id, "teacher_id")
    tenant_oid = _tenant_oid(current_user)
    teacher = await db.teachers.find_one({"_id": teacher_oid, "tenantId": tenant_oid})
    if not teacher:
        raise HTTPException(404, "Teacher not found")
    updated = await crud_update_teacher(id, updates.dict(exclude_unset=True))
    if not updated:
        raise HTTPException(404, "Teacher not found")
    return updated


@router.delete("/teachers/{teacher_id}")
async def delete_teacher(teacher_id: str, current_user=Depends(get_current_user)):
    teacher_oid = _to_oid(teacher_id, "teacher_id")
    tenant_oid = _tenant_oid(current_user)
    teacher = await db.teachers.find_one({"_id": teacher_oid, "tenantId": tenant_oid})
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    # crud_delete_teacher handles both teacher and user documents
    success = await crud_delete_teacher(teacher_id)
    if not success:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return {"id": teacher_id, "message": "Teacher deleted successfully"}


# ------------------ Courses Endpoints ------------------


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, data: dict, current_user=Depends(get_current_user)):
    course_oid = _to_oid(course_id, "course_id")
    tenant_oid = _tenant_oid(current_user)
    course = await db.courses.find_one({"_id": course_oid, "tenantId": tenant_oid})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    update_data = {k: v for k, v in data.items() if v is not None}
    if update_data:
        update_data["updatedAt"] = datetime.utcnow()
        await db.courses.update_one({"_id": course_oid}, {"$set": update_data})

    updated_course = await db.courses.find_one({"_id": course_oid, "tenantId": tenant_oid})

    return {
        "id": str(updated_course["_id"]),
        "title": updated_course.get("title", ""),
        "code": updated_course.get("courseCode", ""),
        "instructor": updated_course.get("instructor", "N/A"),
        "status": updated_course.get("status", "Active"),
    }


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str, current_user=Depends(get_current_user)):
    course_oid = _to_oid(course_id, "course_id")
    tenant_oid = _tenant_oid(current_user)
    result = await db.courses.delete_one({"_id": course_oid, "tenantId": tenant_oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Course not found")
    return {"message": "Course deleted successfully"}
