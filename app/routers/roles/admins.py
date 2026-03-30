from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel
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


# ─── Billing & Usage Reporting ────────────────────────────────────────

@router.get("/billing/usage")
async def get_tenant_billing_usage(current_user=Depends(get_current_user)):
    tenant_id = _tenant_oid(current_user)
    tenant = await db.tenants.find_one({"_id": tenant_id, "isDeleted": {"$ne": True}})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant context not found")

    subscription_id = tenant.get("subscriptionId")
    if not subscription_id:
        plan = {
            "name": "Fallback Free",
            "maxStudents": 50,
            "maxTeachers": 5,
            "maxCourses": 10,
            "storageGb": 2,
            "pricePerMonth": 0,
        }
    else:
        plan = await db.subscriptionPlans.find_one({
            "_id": ObjectId(subscription_id), "isDeleted": {"$ne": True}
        })
        if not plan:
            plan = {
                "name": "Unknown Expired Plan",
                "maxStudents": 0, "maxTeachers": 0, "maxCourses": 0, "storageGb": 0, "pricePerMonth": 0
            }
        else:
            plan["id"] = str(plan["_id"])
            plan.pop("_id", None)

    # To accurately count students, we must match natively created students + students globally enrolled in this tenant's courses.
    tenant_courses = [c["_id"] async for c in db.courses.find({"tenantId": tenant_id}, {"_id": 1})]
    students_used = await db.students.count_documents({
        "$or": [
            {"tenantId": tenant_id},
            {"enrolledCourses": {"$in": [str(c) for c in tenant_courses] + tenant_courses}}
        ]
    })
    
    teachers_used = await db.teachers.count_documents({"tenantId": tenant_id})
    courses_used = await db.courses.count_documents({"tenantId": tenant_id})
    storage_used_bytes = tenant.get("totalStorageUsedBytes", 0)

    # Convert bytes to GBs loosely 
    storage_used_gb = round(storage_used_bytes / (1024 * 1024 * 1024), 4)

    return {
        "plan": plan,
        "usage": {
            "students": students_used,
            "teachers": teachers_used,
            "courses": courses_used,
            "storageGb": storage_used_gb,
            "storageBytes": storage_used_bytes,
        }
    }

@router.get("/billing/plans")
async def get_available_billing_plans(current_user=Depends(get_current_user)):
    cursor = db.subscriptionPlans.find({"isDeleted": False, "status": "active"}).sort("pricePerMonth", 1)
    plans = await cursor.to_list(length=100)
    for p in plans:
        p["id"] = str(p["_id"])
        p.pop("_id", None)
    return plans

class CheckoutPlanRequest(BaseModel):
    planId: str

@router.post("/billing/checkout")
async def create_billing_checkout(req: CheckoutPlanRequest, current_user=Depends(get_current_user)):
    import stripe
    import os
    from datetime import timedelta
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:4200")
    
    tenant_id = _tenant_oid(current_user)
    
    plan = await db.subscriptionPlans.find_one({
        "_id": ObjectId(req.planId), "isDeleted": {"$ne": True}
    })
    if not plan:
        raise HTTPException(status_code=404, detail="Subscription Plan not found")

    price = plan.get("pricePerMonth", 0)
    
    # Free/downgrade plans: apply instantly without Stripe
    if price <= 0:
        now = datetime.utcnow()
        billing_cycle = plan.get("billingCycle", "monthly")
        expiry = now + timedelta(days=365) if billing_cycle == "yearly" else now + timedelta(days=30)
        await db.tenants.update_one(
            {"_id": tenant_id},
            {"$set": {
                "subscriptionId": ObjectId(req.planId),
                "subscriptionStartDate": now,
                "subscriptionExpiryDate": expiry,
                "updatedAt": now
            }}
        )
        return {"success": True, "message": f"Switched to {plan['name']}"}

    # Map billingCycle to Stripe interval
    cycle_map = {"monthly": "month", "yearly": "year", "weekly": "week"}
    raw_cycle = plan.get("billingCycle", "monthly").lower()
    stripe_interval = cycle_map.get(raw_cycle, "month")

    try:
        session = stripe.checkout.Session.create(
            ui_mode="embedded",
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': plan['name'],
                        'description': f"SaaS Upgrade to {plan['name']} tier",
                    },
                    'unit_amount': int(price * 100),
                    'recurring': {
                        'interval': stripe_interval
                    }
                },
                'quantity': 1,
            }],
            mode='subscription',
            return_url=f"{FRONTEND_URL}/admin/settings?billing_success=true",
            metadata={
                "tenantId": str(tenant_id),
                "planId": str(plan["_id"]),
                "type": "tenant_upgrade"
            }
        )
        return {"clientSecret": session.client_secret}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

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


# ------------------ System Settings -----------------


@router.get("/settings/system")
async def get_system_settings(current_user=Depends(get_current_user)):
    tenant_oid = _tenant_oid(current_user)
    tenant = await db.tenants.find_one({"_id": tenant_oid})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "tenantName": tenant.get("tenantName", ""),
        "tenantLogoUrl": tenant.get("tenantLogoUrl", "")
    }


@router.put("/settings/system")
async def update_system_settings(data: dict, current_user=Depends(get_current_user)):
    tenant_oid = _tenant_oid(current_user)
    updates = {}
    if "tenantName" in data and isinstance(data["tenantName"], str):
        updates["tenantName"] = data["tenantName"]
    if "tenantLogoUrl" in data and isinstance(data["tenantLogoUrl"], str):
        updates["tenantLogoUrl"] = data["tenantLogoUrl"]
    
    if updates:
        updates["updatedAt"] = datetime.utcnow()
        await db.tenants.update_one({"_id": tenant_oid}, {"$set": updates})
        
    updated_tenant = await db.tenants.find_one({"_id": tenant_oid})
    return {
        "tenantName": updated_tenant.get("tenantName", ""),
        "tenantLogoUrl": updated_tenant.get("tenantLogoUrl", "")
    }


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
