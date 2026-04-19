from bson import ObjectId
from datetime import datetime
from app.db.database import db
from app.crud.users import serialize_user
from app.utils.security import hash_password, verify_password
from app.utils.exceptions import not_found, bad_request
from app.utils.tenant_students import count_tenant_students, get_tenant_course_ids


def serialize_superadmin(user_doc):
    return {
        "id": str(user_doc["_id"]),
        "userId": str(user_doc["_id"]),
        "user": serialize_user(user_doc),  # attach user details
        "createdAt": user_doc["createdAt"],
        "updatedAt": user_doc["updatedAt"],
    }


async def get_superadmin_by_user(user_id: str):
    user = await db.users.find_one(
        {"_id": ObjectId(user_id), "role": {"$in": ["super_admin", "super-admin"]}}
    )
    if not user:
        return None
    return serialize_superadmin(user)


ROLE_VALUES = ["super_admin", "super-admin"]


async def update_superadmin(user_id: str, updates: dict):
    allowed_fields = ["fullName", "profileImageURL", "contactNo", "country", "status"]
    user_fields = {k: v for k, v in updates.items() if k in allowed_fields}

    if user_fields:
        user_fields["updatedAt"] = datetime.utcnow()
        user_fields["role"] = "super_admin"
        result = await db.users.update_one(
            {"_id": ObjectId(user_id), "role": {"$in": ROLE_VALUES}},
            {"$set": user_fields},
        )
        # Optional: check matched_count
        if result.matched_count == 0:
            return None

    # Fetch the updated document
    user = await db.users.find_one(
        {"_id": ObjectId(user_id), "role": {"$in": ROLE_VALUES}}
    )
    if not user:
        return None

    return serialize_superadmin(user)

async def get_super_admin_dashboard_stats():
    # 1. Total Tenants
    total_tenants = await db.tenants.count_documents({"isDeleted": False})
    
    # 2. Active Users = total teachers + total students across all tenants
    total_teachers = await db.teachers.count_documents({})
    total_students = await db.students.count_documents({})
    active_users_count = total_teachers + total_students
    active_users_str = f"{active_users_count/1000:.1f}K" if active_users_count >= 1000 else str(active_users_count)
    
    # 3. Total Courses
    total_courses = await db.courses.count_documents({})
    
    # 4. Revenue (try subscriptions first, fallback to tenant subscription prices)
    pipeline_revenue = [
        {"$group": {"_id": None, "total": {"$sum": "$price_per_month"}}}
    ]
    revenue_cursor = await db.subscriptions.aggregate(pipeline_revenue).to_list(length=1)
    rev_amount = revenue_cursor[0]["total"] if revenue_cursor and revenue_cursor[0].get("total") else 0
    
    # Fallback: sum subscriptionPriceMonthly from tenants if subscriptions collection has no data
    if rev_amount == 0:
        pipeline_tenant_rev = [
            {"$match": {"isDeleted": False, "subscriptionPriceMonthly": {"$exists": True}}},
            {"$group": {"_id": None, "total": {"$sum": "$subscriptionPriceMonthly"}}}
        ]
        tenant_rev_cursor = await db.tenants.aggregate(pipeline_tenant_rev).to_list(length=1)
        rev_amount = tenant_rev_cursor[0]["total"] if tenant_rev_cursor and tenant_rev_cursor[0].get("total") else 0
    
    revenue_str = f"${rev_amount/1000:.1f}K" if rev_amount >= 1000 else f"${rev_amount}"

    # 5. Top Organizations with teachers, students, courses counts
    # Inline the counting logic (mirrors _tenant_metrics + admin_dashboard.get_all_students)
    tenants_cursor = db.tenants.find({"isDeleted": False}).sort("createdAt", -1)
    all_tenants = await tenants_cursor.to_list(length=100)
    
    org_rows_raw = []
    for t in all_tenants:
        tid = t["_id"]
        tenant_course_ids = await get_tenant_course_ids(tid)
        
        # Count teachers (only by tenantId)
        teacher_count = await db.teachers.count_documents({"tenantId": tid})
        student_count = await count_tenant_students(tid)
        
        org_rows_raw.append({
            "name": t.get("tenantName", "Unknown"),
            "teachers": teacher_count,
            "students": student_count,
            "courses": len(tenant_course_ids)
        })
    
    # Sort by students descending, take top 5
    org_rows_raw.sort(key=lambda x: x["students"], reverse=True)
    org_rows = org_rows_raw[:5]

    # Map the top orgs to the Bar Chart data structure (using total members for scale)
    growth_data = [{
        "month": org["name"][:8] + ".." if len(org["name"]) > 8 else org["name"],
        "tenants": org["teachers"] + org["students"]
    } for org in org_rows]



    if not growth_data:
        growth_data = [
            {"month": "Empty", "tenants": 0}
        ]

    # 6. Activity Data (Active / Inactive only)
    active_count = await db.tenants.count_documents({"status": {"$regex": "(?i)^active$"}, "isDeleted": False})
    inactive_count = total_tenants - active_count
    
    activity_data = [
        {"category": "Active", "value": active_count, "color": "bg-green-500"},
        {"category": "Inactive", "value": inactive_count, "color": "bg-red-500"}
    ]



    return {
        "totalTenants": total_tenants,
        "activeUsers": active_users_str,
        "totalCourses": total_courses,
        "revenue": revenue_str,
        "tenantGrowthData": growth_data,
        "activityData": activity_data,
        "organizationRows": org_rows
    }


async def change_superadmin_password(
    current_user: dict, new_password: str
):
    """Super admin can change password without providing old password."""
    user = await db.users.find_one(
        {"_id": ObjectId(current_user["user_id"]), "role": {"$in": ROLE_VALUES}}
    )
    if not user:
        not_found("Super Admin user")
    if verify_password(new_password, user["password"]):
        bad_request("New password must differ from current password")
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "password": hash_password(new_password),
            "updatedAt": datetime.utcnow(),
        }}
    )
    return {"message": "Password updated"}
