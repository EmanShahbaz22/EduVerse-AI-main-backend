from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.auth.dependencies import get_current_user, require_role
from app.db.database import db
from app.crud.student_performance import StudentPerformanceCRUD

router = APIRouter(
    prefix="/studentPerformance",
    tags=["Student Performance"],
    dependencies=[Depends(get_current_user)],
)


async def _enforce_teacher_performance_scope(
    teacher_id: str,
    tenant_id: str,
    current_user: dict,
):
    if not ObjectId.is_valid(teacher_id):
        raise HTTPException(status_code=400, detail="Invalid teacher_id")

    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenantId")

    if current_user["role"] == "super_admin":
        return

    if current_user.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )

    teacher = await db.teachers.find_one({"_id": ObjectId(teacher_id)})
    if not teacher or str(teacher.get("tenantId")) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: teacher is outside your tenant",
        )

    if current_user["role"] == "teacher" and current_user.get("teacher_id") != teacher_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: cannot access another teacher's student data",
        )


async def _enforce_student_performance_access(
    student_id: str,
    tenant_id: str,
    current_user: dict,
):
    if not ObjectId.is_valid(student_id):
        raise HTTPException(status_code=400, detail="Invalid studentId")

    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenantId")

    if current_user["role"] == "super_admin":
        return

    if current_user["role"] == "student":
        if current_user.get("student_id") != student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot access another student's performance",
            )
        return

    if current_user.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )


# -------------------- GLOBAL LEADERBOARDS --------------------
@router.get("/leaderboard/global-full")
async def global_full():
    return await StudentPerformanceCRUD.global_full()


@router.get("/leaderboard/global-top5")
async def global_top5():
    return await StudentPerformanceCRUD.global_top5()


@router.get("/leaderboard/global-summary")
async def global_summary(
    limit: int = Query(10, ge=3, le=25),
    current_user=Depends(require_role("student")),
):
    if current_user.get("student_id"):
        await StudentPerformanceCRUD.sync_global_from_progress(current_user["student_id"])
    return await StudentPerformanceCRUD.global_summary(
        current_user.get("student_id"), limit
    )


@router.get("/me")
async def get_my_student_performance(
    current_user=Depends(require_role("student")),
):
    student_id = current_user.get("student_id")
    if not student_id:
        raise HTTPException(status_code=404, detail="Student profile not found")
    return await StudentPerformanceCRUD.get_global_student_performance(student_id)


# -------------------- TENANT LEADERBOARDS --------------------
@router.get("/{tenantId}/leaderboard")
async def tenant_full(tenantId: str):
    return await StudentPerformanceCRUD.tenant_full(tenantId)


@router.get("/{tenantId}/leaderboard-top5")
async def tenant_top5(tenantId: str):
    return await StudentPerformanceCRUD.tenant_top5(tenantId)


# -------------------- TEACHER SPECIFIC --------------------
@router.get("/teacher/{teacher_id}")
async def get_teacher_student_performances(
    teacher_id: str,
    tenantId: str,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    """
    Get all student performances for a specific teacher's courses.
    Requires tenantId as query parameter.
    """
    await _enforce_teacher_performance_scope(teacher_id, tenantId, current_user)
    return await StudentPerformanceCRUD.get_teacher_performances(teacher_id, tenantId)

@router.get("/teacher/{teacher_id}/student/{student_id}/details")
async def get_detailed_teacher_student_performance(
    teacher_id: str,
    student_id: str,
    tenantId: str,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    """
    Get detailed breakdown of a student's quiz and assignment scores for the teacher's courses.
    """
    await _enforce_teacher_performance_scope(teacher_id, tenantId, current_user)
    from app.crud.detailed_student_performance import get_detailed_student_performance
    return await get_detailed_student_performance(teacher_id, student_id, tenantId)

# -------------------- STUDENT PERFORMANCE --------------------
@router.get("/{tenantId}/{studentId}")
async def get_student_performance(
    tenantId: str,
    studentId: str,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.get_student_performance(studentId, tenantId)


# -------------------- BADGES --------------------
@router.get("/{tenantId}/{studentId}/badges")
async def get_badges(
    tenantId: str,
    studentId: str,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.view_badges(studentId, tenantId)


@router.post(
    "/{tenantId}/{studentId}/badges",
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def add_badge(
    tenantId: str,
    studentId: str,
    badge: dict,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.add_badge(studentId, tenantId, badge)


# -------------------- CERTIFICATES --------------------
@router.get("/{tenantId}/{studentId}/certificates")
async def get_certificates(
    tenantId: str,
    studentId: str,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.view_certificates(studentId, tenantId)


@router.post(
    "/{tenantId}/{studentId}/certificates",
    dependencies=[Depends(require_role("admin", "teacher"))],
)
async def add_certificate(
    tenantId: str,
    studentId: str,
    cert: dict,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.add_certificate(studentId, tenantId, cert)


# -------------------- COURSE STATS --------------------
@router.get("/{tenantId}/{studentId}/course-stats")
async def course_stats(
    tenantId: str,
    studentId: str,
    current_user=Depends(get_current_user),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.get_course_stats(studentId, tenantId)


@router.post("/{tenantId}/{studentId}/course-progress/{courseId}")
async def update_course_progress(
    tenantId: str,
    studentId: str,
    courseId: str,
    completion: int,
    lastActive: str,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.update_course_progress(
        studentId, tenantId, courseId, completion, lastActive
    )


# -------------------- WEEKLY TIME --------------------
@router.post("/{tenantId}/{studentId}/weekly-time")
async def weekly_time(
    tenantId: str,
    studentId: str,
    weekStart: str,
    minutes: int,
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.add_weekly_time(
        studentId, tenantId, weekStart, minutes
    )


# -------------------- POINTS --------------------
@router.post("/{tenantId}/{studentId}/add-points")
async def add_points(
    tenantId: str,
    studentId: str,
    points: int,
    reason: str = "Course Activity",
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    await _enforce_student_performance_access(studentId, tenantId, current_user)
    return await StudentPerformanceCRUD.add_points(studentId, tenantId, points, reason)
