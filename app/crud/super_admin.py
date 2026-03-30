from bson import ObjectId
from datetime import datetime
from app.db.database import db
from app.crud.users import serialize_user


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
    
    # 2. Active Users (assume users with status='Active' or just total if not filtered)
    total_users = await db.users.count_documents({"status": "active"})
    active_users_str = f"{total_users/1000:.1f}K" if total_users >= 1000 else str(total_users)
    
    # 3. Total Courses
    total_courses = await db.courses.count_documents({})
    
    # 4. Revenue (subscriptions)
    pipeline_revenue = [
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    revenue_cursor = await db.subscriptions.aggregate(pipeline_revenue).to_list(length=1)
    rev_amount = revenue_cursor[0]["total"] if revenue_cursor else 0
    revenue_str = f"${rev_amount/1000:.1f}K" if rev_amount >= 1000 else f"${rev_amount}"

    # 5. Top Organizations
    # Top 5 tenants by number of users
    pipeline_orgs = [
        {"$match": {"isDeleted": False}},
        {"$lookup": {"from": "users", "localField": "_id", "foreignField": "tenantId", "as": "users"}},
        {"$lookup": {"from": "courses", "localField": "_id", "foreignField": "tenantId", "as": "courses"}},
        {"$project": {
            "tenantName": 1,
            "users": {"$size": "$users"},
            "activeCourses": {"$size": "$courses"}
        }},
        {"$sort": {"users": -1}},
        {"$limit": 5}
    ]
    orgs_cursor = await db.tenants.aggregate(pipeline_orgs).to_list(length=5)
    org_rows = [{"name": org.get("tenantName", "Unknown"), "activeCourses": org.get("activeCourses", 0), "users": org.get("users", 0)} for org in orgs_cursor]

    # Map the top orgs to the Bar Chart data structure (using 'month' key for the x-axis label)
    growth_data = [{"month": org["name"][:8] + ".." if len(org["name"]) > 8 else org["name"], "tenants": org["users"]} for org in org_rows]

    if not growth_data:
        growth_data = [
            {"month": "Empty", "tenants": 0}
        ]

    # 6. Activity Data
    active_count = await db.tenants.count_documents({"status": {"$regex": "(?i)^active$"}, "isDeleted": False})
    pending_count = await db.tenants.count_documents({"status": {"$regex": "(?i)^pending$"}, "isDeleted": False})
    inactive_count = await db.tenants.count_documents({"status": {"$regex": "(?i)^inactive$"}, "isDeleted": False})
    
    # If a tenant has missing/null status, arbitrarily classify as Inactive
    classified_sum = active_count + pending_count + inactive_count
    if classified_sum < total_tenants:
        inactive_count += (total_tenants - classified_sum)
    
    activity_data = [
        {"category": "Active", "value": active_count, "color": "bg-green-500"},
        {"category": "Pending", "value": pending_count, "color": "bg-yellow-500"},
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
