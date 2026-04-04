from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from app.schemas.courses import (
    CourseCreate,
    CourseUpdate,
    CourseResponse,
    CourseMetadataResponse,
    CourseCategorySettingsUpdate,
    CourseEnrollment,
    ReorderLessonsRequest,
    ReorderModulesRequest,
    PublishCourseRequest,
    CourseWithProgress,
)
from app.crud.courses import course_crud
from app.auth.dependencies import get_current_user, require_role
from app.db.database import db
from app.core.course_metadata import (
    get_course_metadata,
    update_tenant_custom_categories,
)

router = APIRouter(
    prefix="/courses", tags=["courses"], dependencies=[Depends(get_current_user)]
)


def _raise_on_error(result, key="message"):
    """Map CRUD error messages to proper HTTP status codes."""
    msg = result[key]
    if "Invalid" in msg and "format" in msg:
        raise HTTPException(400, msg)
    if "different tenant" in msg:
        raise HTTPException(403, msg)
    if "not found" in msg:
        raise HTTPException(404, msg)
    raise HTTPException(400, msg)


def _enforce_tenant_scope(current_user: dict, tenant_id: str):
    if current_user["role"] == "super_admin":
        return
    if not current_user.get("tenant_id") or current_user["tenant_id"] != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )


async def _resolve_enrollment_context(enrollment: CourseEnrollment, current_user: dict):
    role = current_user["role"]
    if role == "student":
        student = await db.students.find_one({"userId": ObjectId(current_user["user_id"])})
        if not student:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Student profile not found",
            )
        # Students are tenant-independent; tenant scope is resolved from the target course.
        return str(student["_id"]), None, False

    if role in {"admin", "teacher"}:
        tenant_id = current_user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant context required",
            )
        if enrollment.tenantId and enrollment.tenantId != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: tenant mismatch",
            )
        if not enrollment.studentId or not ObjectId.is_valid(enrollment.studentId):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Valid studentId is required",
            )
        student = await db.students.find_one(
            {"_id": ObjectId(enrollment.studentId), "tenantId": ObjectId(tenant_id)}
        )
        if not student:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: student belongs to a different tenant",
            )
        return enrollment.studentId, tenant_id, True

    if role == "super_admin":
        if not enrollment.studentId or not ObjectId.is_valid(enrollment.studentId):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Valid studentId is required",
            )
        student = await db.students.find_one({"_id": ObjectId(enrollment.studentId)})
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        tenant_id = enrollment.tenantId
        if not tenant_id:
            if not ObjectId.is_valid(enrollment.courseId):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid courseId format",
                )
            course = await db.courses.find_one({"_id": ObjectId(enrollment.courseId)})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")
            tenant_value = course.get("tenantId")
            if not tenant_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Course tenant context missing",
                )
            tenant_id = str(tenant_value)
        return enrollment.studentId, tenant_id, False

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: insufficient role",
    )


@router.post(
    "/",
    response_model=CourseResponse,
    status_code=201,
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def create_course(course: CourseCreate):
    try:
        return await course_crud.create_course(course)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/", response_model=List[CourseResponse])
async def get_courses(
    tenantId: Optional[str] = Query(None),
    teacher_id: Optional[str] = Query(None),
    course_status: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    if current_user["role"] == "student" or not tenantId:
        result = await course_crud.get_marketplace_courses(
            tenant_id=tenantId,
            teacher_id=teacher_id,
            category=category,
            search=search,
            skip=skip,
            limit=limit,
        )
    else:
        if not tenantId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenantId is required for this role",
            )
        result = await course_crud.get_all_courses(
            tenant_id=tenantId,
            teacher_id=teacher_id,
            status=course_status,
            category=category,
            search=search,
            skip=skip,
            limit=limit,
        )
    if not result["success"]:
        raise HTTPException(400, result["message"])
    return result["courses"]


@router.get("/metadata", response_model=CourseMetadataResponse)
async def get_course_metadata_options(current_user=Depends(get_current_user)):
    return await get_course_metadata(current_user.get("tenant_id"))


@router.put(
    "/metadata/categories",
    response_model=CourseMetadataResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def update_course_metadata_categories(
    payload: CourseCategorySettingsUpdate,
    current_user=Depends(get_current_user),
):
    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )

    try:
        return await update_tenant_custom_categories(tenant_id, payload.categories)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: str,
    tenantId: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    if current_user["role"] == "student":
        result = await course_crud.get_course_by_id_any_tenant(
            course_id, public_only=True
        )
    elif tenantId:
        result = await course_crud.get_course_by_id(course_id, tenantId)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenantId is required for this role",
        )
    if not result["success"]:
        _raise_on_error(result)
    return result["course"]


@router.put(
    "/{course_id}",
    response_model=CourseResponse,
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def update_course(
    course_id: str, course_update: CourseUpdate, tenantId: str = Query(...)
):
    updated = await course_crud.update_course(course_id, tenantId, course_update)
    if not updated:
        raise HTTPException(404, "Course not found or wrong tenant")
    return updated


@router.delete(
    "/{course_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def delete_course(course_id: str, tenantId: str = Query(...)):
    result = await course_crud.delete_course(course_id, tenantId)
    if not result["success"]:
        _raise_on_error(result)
    return None


@router.post("/enroll", status_code=200)
async def enroll_in_course(
    enrollment: CourseEnrollment,
    current_user=Depends(require_role("student", "admin", "teacher", "super_admin")),
):
    student_id, tenant_id, enforce_same_tenant = await _resolve_enrollment_context(
        enrollment, current_user
    )
    result = await course_crud.enroll_student(
        enrollment.courseId,
        student_id,
        tenant_id,
        enforce_same_tenant=enforce_same_tenant,
    )
    if not result["success"]:
        if "different tenant" in result["message"]:
            raise HTTPException(403, result["message"])
        raise HTTPException(400, result["message"])
    return result


@router.post("/unenroll", status_code=200)
async def unenroll_from_course(
    enrollment: CourseEnrollment,
    current_user=Depends(require_role("student", "admin", "teacher", "super_admin")),
):
    student_id, tenant_id, enforce_same_tenant = await _resolve_enrollment_context(
        enrollment, current_user
    )
    result = await course_crud.unenroll_student(
        enrollment.courseId,
        student_id,
        tenant_id,
        enforce_same_tenant=enforce_same_tenant,
    )
    if not result["success"]:
        if "different tenant" in result["message"]:
            raise HTTPException(403, result["message"])
        raise HTTPException(400, result["message"])
    return result


@router.get("/{course_id}/students")
async def get_course_students(
    course_id: str,
    tenantId: str = Query(...),
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    _enforce_tenant_scope(current_user, tenantId)
    result = await course_crud.get_enrolled_students(course_id, tenantId)
    if not result["success"]:
        _raise_on_error(result)
    return result["students"]


@router.delete("/{course_id}/students/{student_id}", status_code=200)
async def unenroll_student_from_course(
    course_id: str,
    student_id: str,
    tenantId: str = Query(...),
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    _enforce_tenant_scope(current_user, tenantId)
    result = await course_crud.unenroll_student(course_id, student_id, tenantId)
    if not result["success"]:
        if "different tenant" in result["message"]:
            raise HTTPException(403, result["message"])
        raise HTTPException(400, result["message"])
    return result


@router.get("/student/{student_id}", response_model=List[CourseWithProgress])
async def get_student_courses(
    student_id: str,
    tenantId: Optional[str] = Query(None),
    current_user=Depends(require_role("student", "admin", "teacher", "super_admin")),
):
    if current_user["role"] == "student":
        if current_user.get("student_id") != student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot access another student's courses",
            )
        result = (
            await course_crud.get_student_courses(student_id, tenantId)
            if tenantId
            else await course_crud.get_student_courses_any_tenant(student_id)
        )
    else:
        if not tenantId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenantId is required for this role",
            )
        _enforce_tenant_scope(current_user, tenantId)
        result = await course_crud.get_student_courses(student_id, tenantId)
    if not result["success"]:
        _raise_on_error(result)
    return result["courses"]


@router.patch("/{course_id}/reorder/lessons", response_model=CourseResponse)
async def reorder_lessons(
    course_id: str, req: ReorderLessonsRequest, tenantId: str = Query(...)
):
    result = await course_crud.reorder_lessons(
        course_id, tenantId, req.moduleId, req.lessonIds
    )
    if not result["success"]:
        _raise_on_error(result)
    return result["course"]


@router.patch("/{course_id}/reorder/modules", response_model=CourseResponse)
async def reorder_modules(
    course_id: str, req: ReorderModulesRequest, tenantId: str = Query(...)
):
    result = await course_crud.reorder_modules(course_id, tenantId, req.moduleIds)
    if not result["success"]:
        _raise_on_error(result)
    return result["course"]


@router.post(
    "/{course_id}/publish",
    response_model=CourseResponse,
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def publish_course(
    course_id: str, req: PublishCourseRequest, tenantId: str = Query(...)
):
    result = await course_crud.publish_course(course_id, tenantId, req.publish)
    if not result["success"]:
        _raise_on_error(result)
    return result["course"]
