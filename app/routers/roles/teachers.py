from bson import ObjectId
import csv
import io
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.schemas.teachers import (
    TeacherCreate,
    TeacherBulkInviteRequest,
    TeacherBulkInviteResponse,
    TeacherUpdate,
    TeacherResponse,
    ChangePassword,
)
from app.crud.teachers import (
    change_teacher_me_password,
    create_or_link_teacher_for_tenant,
    create_teacher,
    get_all_teachers,
    get_teacher_me,
    delete_teacher,
    get_teacher_students,
    get_teacher_dashboard,
    update_teacher_me,
)
from app.auth.dependencies import get_current_user, require_role
from app.crud.teachers import to_oid
from app.db.database import db

router = APIRouter(
    prefix="/teachers",
    tags=["Teachers"],
)

CSV_MAX_SIZE_BYTES = 2 * 1024 * 1024  # 2MB

# ------------------ Profile (Me) ------------------


@router.get("/me", response_model=TeacherResponse)
async def me(current_user=Depends(require_role("teacher"))):
    return await get_teacher_me(current_user)


@router.patch("/me", response_model=TeacherResponse)
async def update_me(
    payload: TeacherUpdate,
    current_user=Depends(require_role("teacher")),
):
    return await update_teacher_me(current_user, payload)


@router.put("/me/password")
async def change_my_password(
    payload: ChangePassword,
    current_user=Depends(require_role("teacher")),
):
    await change_teacher_me_password(
        current_user, payload.oldPassword, payload.newPassword
    )


def validate_object_id(id: str, name="id"):
    if not ObjectId.is_valid(id):
        raise HTTPException(400, f"Invalid ObjectId for {name}")


def _resolve_target_tenant(current_user: dict, tenant_id_hint: str | None) -> str:
    if current_user["role"] == "super_admin":
        if not tenant_id_hint:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenantId is required for super-admin requests",
            )
        validate_object_id(tenant_id_hint, "tenantId")
        return tenant_id_hint

    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )
    validate_object_id(tenant_id, "tenantId")
    return tenant_id


async def _get_teacher_doc(teacher_id: str):
    teacher = await db.teachers.find_one({"_id": to_oid(teacher_id, "teacherId")})
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return teacher


async def _enforce_teacher_scope(teacher_id: str, current_user: dict):
    teacher = await _get_teacher_doc(teacher_id)
    if current_user["role"] != "super_admin":
        if str(teacher.get("tenantId")) != current_user.get("tenant_id"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: tenant mismatch",
            )
        if current_user["role"] == "teacher" and str(teacher.get("userId")) != str(
            current_user["user_id"]
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: you can only access your own teacher profile",
            )
    return teacher


# ------------------ CRUD ------------------


@router.post("/", response_model=TeacherResponse)
async def create_teacher_route(
    data: TeacherCreate,
    current_user=Depends(require_role("admin", "super_admin")),
):
    if current_user["role"] != "super_admin" and data.tenantId != current_user.get(
        "tenant_id"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: tenant mismatch",
        )
    return await create_teacher(data)


@router.post("/bulk-invite", response_model=TeacherBulkInviteResponse)
async def bulk_invite_teachers_route(
    payload: TeacherBulkInviteRequest,
    current_user=Depends(require_role("admin", "super_admin")),
):
    tenant_id = _resolve_target_tenant(current_user, payload.tenantId)
    if not payload.emails:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one email is required",
        )

    created = 0
    linked = 0
    skipped = 0
    errors: list[str] = []
    generated_passwords: dict[str, str] = {}

    for raw_email in payload.emails:
        email = str(raw_email).strip().lower()
        if not email:
            skipped += 1
            continue
        try:
            _, mode, generated_password = await create_or_link_teacher_for_tenant(
                tenant_id=tenant_id,
                email=email,
                full_name=email.split("@")[0],
                password=payload.defaultPassword,
                status=payload.status,
            )
            if mode == "created":
                created += 1
                if generated_password:
                    generated_passwords[email] = generated_password
            elif mode == "linked_existing":
                linked += 1
            else:
                skipped += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
            errors.append(f"{email}: {detail}")
        except Exception:
            errors.append(f"{email}: unexpected server error")

    return TeacherBulkInviteResponse(
        created=created,
        linkedExisting=linked,
        skipped=skipped,
        errors=errors,
        generatedPasswords=generated_passwords,
    )


@router.post("/bulk-upload-csv", response_model=TeacherBulkInviteResponse)
async def bulk_upload_teachers_csv_route(
    file: UploadFile = File(...),
    defaultPassword: str | None = Form(None),
    statusValue: str = Form("active"),
    tenantId: str | None = Form(None),
    current_user=Depends(require_role("admin", "super_admin")),
):
    tenant_id = _resolve_target_tenant(current_user, tenantId)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported",
        )

    content = await file.read()
    if len(content) > CSV_MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file exceeds 2MB limit",
        )

    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV must be UTF-8 encoded",
        )

    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames or "email" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV must include an 'email' column",
        )

    created = 0
    linked = 0
    skipped = 0
    errors: list[str] = []
    generated_passwords: dict[str, str] = {}

    for index, row in enumerate(reader, start=2):
        email = str((row.get("email") or "").strip()).lower()
        if not email:
            skipped += 1
            continue

        full_name = (row.get("fullName") or row.get("name") or email.split("@")[0]).strip()
        contact_no = (row.get("contactNo") or "").strip() or None
        country = (row.get("country") or "").strip() or None
        row_status = (row.get("status") or statusValue or "active").strip() or "active"
        row_password = (row.get("password") or defaultPassword or "").strip() or None

        qualifications_raw = (row.get("qualifications") or "").strip()
        subjects_raw = (row.get("subjects") or "").strip()
        qualifications = [v.strip() for v in qualifications_raw.split("|") if v.strip()]
        subjects = [v.strip() for v in subjects_raw.split("|") if v.strip()]

        try:
            _, mode, generated_password = await create_or_link_teacher_for_tenant(
                tenant_id=tenant_id,
                email=email,
                full_name=full_name,
                password=row_password,
                contact_no=contact_no,
                country=country,
                status=row_status,
                qualifications=qualifications,
                subjects=subjects,
            )
            if mode == "created":
                created += 1
                if generated_password:
                    generated_passwords[email] = generated_password
            elif mode == "linked_existing":
                linked += 1
            else:
                skipped += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
            errors.append(f"line {index} ({email}): {detail}")
        except Exception:
            errors.append(f"line {index} ({email}): unexpected server error")

    return TeacherBulkInviteResponse(
        created=created,
        linkedExisting=linked,
        skipped=skipped,
        errors=errors,
        generatedPasswords=generated_passwords,
    )


@router.get("/", response_model=list[TeacherResponse])
async def get_all_teachers_route(
    current_user=Depends(require_role("admin", "teacher", "super_admin")),
):
    tenant_id = None if current_user["role"] == "super_admin" else current_user.get(
        "tenant_id"
    )
    return await get_all_teachers(tenant_id)


# @router.get("/{id}", response_model=TeacherResponse)
# async def get_teacher_route(id: str):
#     validate_object_id(id)
#     t = await get_teacher(id)
#     if not t:
#         raise HTTPException(404, "Teacher not found")
#     return t


@router.delete("/{id}")
async def delete_teacher_route(
    id: str, current_user=Depends(require_role("admin", "super_admin"))
):
    validate_object_id(id)
    await _enforce_teacher_scope(id, current_user)
    result = await delete_teacher(id)
    if not result:
        raise HTTPException(404, "Teacher not found")
    return {"message": "Teacher deleted successfully"}


# ------------------ Dashboard & Students ------------------


@router.get("/{id}/students")
async def teacher_students_route(
    id: str, current_user=Depends(require_role("admin", "teacher", "super_admin"))
):
    validate_object_id(id)
    await _enforce_teacher_scope(id, current_user)
    students = await get_teacher_students(id)
    return {"total": len(students), "students": students}


@router.get("/{id}/dashboard")
async def teacher_dashboard_route(
    id: str, current_user=Depends(require_role("admin", "teacher", "super_admin"))
):
    validate_object_id(id)
    await _enforce_teacher_scope(id, current_user)
    stats = await get_teacher_dashboard(id)
    return stats


# ------------------ Placeholder Integration ------------------


@router.get("/{id}/assignments")
async def teacher_assignments(
    id: str, current_user=Depends(require_role("admin", "teacher", "super_admin"))
):
    await _enforce_teacher_scope(id, current_user)
    return {"message": f"Fetch assignments for teacher {id}"}


@router.get("/{id}/courses")
async def teacher_courses(
    id: str, current_user=Depends(require_role("admin", "teacher", "super_admin"))
):
    await _enforce_teacher_scope(id, current_user)
    return {"message": f"Fetch courses for teacher {id}"}


@router.get("/{id}/quizzes")
async def teacher_quizzes(
    id: str, current_user=Depends(require_role("admin", "teacher", "super_admin"))
):
    await _enforce_teacher_scope(id, current_user)
    return {"message": f"Fetch quizzes for teacher {id}"}
