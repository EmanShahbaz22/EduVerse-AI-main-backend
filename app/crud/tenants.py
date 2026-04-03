from fastapi import HTTPException, status
from app.db.database import db
from datetime import datetime
from bson import ObjectId
from typing import Optional, Any
from calendar import month_abbr
import re


def _ensure_objectid(_id: str, name: str = "id"):
    if not ObjectId.is_valid(_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ObjectId for {name}",
        )
    return ObjectId(_id)


# -------------------------
# Convert MongoDB document → API format (Python dict)
# -------------------------
def serialize_tenant(tenant: dict, metrics: Optional[dict] = None, plan_doc: Optional[dict] = None) -> dict:
    metrics = metrics or {}
    raw_logo = tenant.get("tenantLogoUrl")
    tenant_logo = str(raw_logo).strip() if raw_logo is not None else None
    if not tenant_logo:
        tenant_logo = None
        
    # Dynamically resolve plan info if plan_doc is provided
    plan_name = plan_doc.get("name") if plan_doc else tenant.get("subscriptionPlan")
    plan_category = plan_doc.get("category", "free") if plan_doc else tenant.get("subscriptionCategory", "free")
    plan_cycle = plan_doc.get("billingCycle") if plan_doc else tenant.get("subscriptionBillingCycle")
    plan_price = plan_doc.get("pricePerMonth") if plan_doc else tenant.get("subscriptionPriceMonthly")
    
    return {
        "id": str(tenant["_id"]),  # convert ObjectId -> string
        "tenantName": tenant["tenantName"],
        "tenantLogoUrl": tenant_logo,
        "adminEmail": tenant["adminEmail"],
        "status": tenant.get("status", "active"),
        "contactNumber": tenant.get("contactNumber"),
        "address": tenant.get("address"),
        "subscriptionId": (
            str(tenant.get("subscriptionId")) if tenant.get("subscriptionId") else None
        ),
        "subscriptionCategory": plan_category,
        "subscriptionPlan": plan_name,
        "subscriptionBillingCycle": plan_cycle,
        "subscriptionPriceMonthly": plan_price,
        "subscriptionStartDate": tenant.get("subscriptionStartDate"),
        "subscriptionExpiryDate": tenant.get("subscriptionExpiryDate"),
        "gracePeriodUntil": tenant.get("gracePeriodUntil"),
        "subscriptionNotes": tenant.get("subscriptionNotes"),
        "courses": int(metrics.get("courses", 0)),
        "teachers": int(metrics.get("teachers", 0)),
        "students": int(metrics.get("students", 0)),
        "createdAt": tenant["createdAt"],  # datetime object
        "updatedAt": tenant.get("updatedAt"),
    }


async def is_subscription_active(tenant: dict | ObjectId | str) -> bool:
    """
    Checks if the tenant's subscription is active considering:
    1. Standard Expiry + 48-hour auto-grace (automatic).
    2. Manual Grace Period (gracePeriodUntil).
    """
    from datetime import timedelta
    
    if isinstance(tenant, (ObjectId, str)):
        oid = ObjectId(tenant) if isinstance(tenant, str) else tenant
        tenant = await db.tenants.find_one({"_id": oid, "isDeleted": False})
        
    if not tenant:
        return False
        
    if tenant.get("status") != "active":
        return False
        
    now = datetime.utcnow()
    expiry = tenant.get("subscriptionExpiryDate")
    grace = tenant.get("gracePeriodUntil")
    
    # 1. Check manual grace (priority)
    if grace and now < grace:
        return True
        
    # 2. Check standard expiry + 48h auto-grace
    if expiry:
        # Standard 48-hour grace period
        if now < (expiry + timedelta(hours=48)):
            return True
            
    # If no expiry is set at all (e.g. legacy or custom manual), we assume active 
    # unless logic dictates otherwise. For this system, we'll assume expiry is required for paid/trial.
    if not expiry:
        return True
        
    return False

async def check_tenant_limit(tenant_id: str | ObjectId, resource_type: str) -> bool:
    """
    Checks if a tenant can create more of a resource (students, teachers, courses).
    Returns True if allowed, raises 403 or 402 if limit reached or expired.
    Uses centralized utility in app/utils/limits.py
    """
    from app.utils.limits import check_tenant_limits
    # check_tenant_limits expects lowercase plural or specific strings
    res_map = {"Students": "students", "Teachers": "teachers", "Courses": "courses"}
    await check_tenant_limits(tenant_id, res_map.get(resource_type, resource_type.lower()))
    return True

async def check_and_update_tenant_status(tenant_id: str | ObjectId):
    """
    Called periodically or on-demand to sync the 'status' field with actual expiry/grace logic.
    """
    tenant_oid = ObjectId(tenant_id) if isinstance(tenant_id, str) else tenant_id
    tenant = await db.tenants.find_one({"_id": tenant_oid, "isDeleted": False})
    if not tenant:
        return

    is_active = await is_subscription_active(tenant)
    new_status = "active" if is_active else "inactive"
    
    if tenant.get("status") != new_status:
        await db.tenants.update_one(
            {"_id": tenant_oid}, 
            {"$set": {"status": new_status, "updatedAt": datetime.utcnow()}}
        )

async def _tenant_metrics(tenant_id: ObjectId) -> dict:
    tenant_courses = [doc["_id"] async for doc in db.courses.find({"tenantId": tenant_id}, {"_id": 1})]
    match_query = {
        "$or": [
            {"tenantId": tenant_id},
            {"enrolledCourses": {"$in": [str(c) for c in tenant_courses] + tenant_courses}}
        ]
    }
    return {
        "courses": len(tenant_courses),
        "teachers": await db.teachers.count_documents({"tenantId": tenant_id}),
        "students": await db.students.count_documents(match_query),
    }


# -------------------------
# Create a new tenant
# -------------------------
async def create_tenant(request):
    # Convert Pydantic model to dictionary
    data = request.dict()

    # duplicate check: any tenant with same name that isn't soft-deleted
    existing = await db.tenants.find_one(
        {"tenantName": data["tenantName"], "isDeleted": {"$ne": True}}
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant name already exists"
        )

    if data.get("subscriptionId"):
        _ensure_objectid(data["subscriptionId"], "subscriptionId")
        data["subscriptionId"] = ObjectId(data["subscriptionId"])
    else:
        data.pop("subscriptionId", None)

    # # validate subscriptionId
    # _ensure_objectid(data["subscriptionId"], "subscriptionId")

    # Convert HttpUrl to string if present
    if "tenantLogoUrl" in data and data["tenantLogoUrl"]:
        data["tenantLogoUrl"] = str(data["tenantLogoUrl"])

    data.update(
        {
            # "subscriptionId": ObjectId(data["subscriptionId"]),  # convert to ObjectId
            "createdAt": datetime.utcnow(),  # timestamp
            "status": "active",  # default status
            "subscriptionCategory": data.get("subscriptionCategory", "free"),
            "updatedAt": None,
            "isDeleted": False,
        }
    )

    # Insert into MongoDB
    result = await db.tenants.insert_one(data)

    # Fetch the created tenant
    new_tenant = await db.tenants.find_one({"_id": result.inserted_id})

    return serialize_tenant(new_tenant)


# -------------------------
# Get all tenants (filter, search, sort, pagination)
# -------------------------
async def get_all_tenants(
    skip: int = 0,
    limit: int = 10,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
    plan_code: Optional[str] = None,
    plan_category: Optional[str] = None,
):

    query: dict[str, Any] = {"isDeleted": False}

    # Filter by status
    if status:
        query["status"] = {"$regex": f"^{re.escape(status.strip())}$", "$options": "i"}

    # Case-insensitive search on tenantName & adminEmail
    if search:
        query["$or"] = [
            {"tenantName": {"$regex": search, "$options": "i"}},
            {"adminEmail": {"$regex": search, "$options": "i"}},
        ]

    if plan_category:
        query["subscriptionCategory"] = {
            "$regex": f"^{re.escape(plan_category.strip())}$",
            "$options": "i",
        }

    if plan_code:
        plan_value = plan_code.strip()
        if plan_value:
            query["subscriptionPlan"] = {
                "$regex": f"^{re.escape(plan_value)}$",
                "$options": "i",
            }

    cursor = db.tenants.find(query)

    # Sorting logic
    if sort:
        direction = -1 if sort.startswith("-") else 1
        field = sort.lstrip("-")
        field_map = {
            "name": "tenantName",
            "tenantName": "tenantName",
            "createdAt": "createdAt",
            "status": "status",
            "subscriptionPlan": "subscriptionPlan",
            "subscriptionCategory": "subscriptionCategory",
        }
        field = field_map.get(field, field)
        cursor = cursor.sort(field, direction)

    # Pagination
    tenants = await cursor.skip(skip).limit(limit).to_list(length=limit)

    results = []
    for tenant in tenants:
        metrics = await _tenant_metrics(tenant["_id"])
        plan_doc = None
        if tenant.get("subscriptionId"):
             plan_doc = await db.subscriptionPlans.find_one({"_id": tenant["subscriptionId"]})
        results.append(serialize_tenant(tenant, metrics, plan_doc))
    return results


# -------------------------
# Get a single tenant by id
# -------------------------
async def get_tenant(_id: str):
    _ensure_objectid(_id, "tenantId")
    tenant = await db.tenants.find_one({"_id": ObjectId(_id), "isDeleted": False})
    if not tenant:
        return None
    metrics = await _tenant_metrics(tenant["_id"])
    plan_doc = None
    if tenant.get("subscriptionId"):
         plan_doc = await db.subscriptionPlans.find_one({"_id": tenant["subscriptionId"]})
    return serialize_tenant(tenant, metrics, plan_doc)


# -------------------------
# Update tenant by id
# -------------------------
async def update_tenant(_id: str, updates: dict):

    if not updates:
        return None

    # only include fields with meaningful values
    safe_updates = {}

    for key, val in updates.items():
        if val is None:  # skip empty / null / ""
            continue

        if val == "":  # skip empty strings
            continue

        safe_updates[key] = val

    # if tenantLogoUrl present, convert HttpUrl to string
    if "tenantLogoUrl" in safe_updates:
        safe_updates["tenantLogoUrl"] = str(safe_updates["tenantLogoUrl"])

    # validate and convert subscriptionId if present
    if "subscriptionId" in safe_updates:
        _ensure_objectid(safe_updates["subscriptionId"], "subscriptionId")
        safe_updates["subscriptionId"] = ObjectId(safe_updates["subscriptionId"])

    safe_updates["updatedAt"] = datetime.utcnow()

    await db.tenants.update_one(
        {"_id": ObjectId(_id), "isDeleted": False}, {"$set": safe_updates}
    )

    # Propagate changes to the default Admin associated with this Tenant
    admin_updates = {}
    if "contactNumber" in safe_updates:
        admin_updates["contactNo"] = safe_updates["contactNumber"]
    if "address" in safe_updates:
        admin_updates["country"] = safe_updates["address"]
        
    if admin_updates:
        await db.users.update_one(
            {"tenantId": ObjectId(_id), "role": "admin"},
            {"$set": admin_updates}
        )

    tenant = await db.tenants.find_one({"_id": ObjectId(_id), "isDeleted": False})
    if not tenant:
        return None
    metrics = await _tenant_metrics(tenant["_id"])
    plan_doc = None
    if tenant.get("subscriptionId"):
         plan_doc = await db.subscriptionPlans.find_one({"_id": tenant["subscriptionId"]})
    return serialize_tenant(tenant, metrics, plan_doc)


# -------------------------
# Delete tenant by id
# -------------------------
async def delete_tenant(_id):
    # soft delete
    result = await db.tenants.update_one(
        {"_id": ObjectId(_id)},
        {"$set": {"isDeleted": True, "updatedAt": datetime.utcnow()}},
    )
    return result.modified_count > 0


def _month_window(months: int) -> list[tuple[int, int]]:
    now = datetime.utcnow()
    year = now.year
    month = now.month
    window: list[tuple[int, int]] = []
    for _ in range(months):
        window.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    window.reverse()
    return window


async def get_tenant_dashboard_overview(months: int = 6, top_n: int = 5) -> dict:
    months = max(1, min(months, 24))
    top_n = max(1, min(top_n, 20))

    tenant_query: dict[str, Any] = {"isDeleted": False}

    total_tenants = await db.tenants.count_documents(tenant_query)
    active_users = await db.users.count_documents(
        {"status": {"$in": ["active", "studying", "Active", "Studying"]}}
    )
    total_courses = await db.courses.count_documents({})

    revenue = 0.0
    async for row in db.payments.aggregate(
        [
            {"$match": {"status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
    ):
        revenue = float(row.get("total") or 0.0)

    window = _month_window(months)
    first_year, first_month = window[0]
    window_start = datetime(first_year, first_month, 1)

    growth_map: dict[tuple[int, int], int] = {}
    async for row in db.tenants.aggregate(
        [
            {"$match": {"isDeleted": False, "createdAt": {"$gte": window_start}}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$createdAt"},
                        "m": {"$month": "$createdAt"},
                    },
                    "count": {"$sum": 1},
                }
            },
        ]
    ):
        key = (int(row["_id"]["y"]), int(row["_id"]["m"]))
        growth_map[key] = int(row.get("count", 0))

    tenant_growth = [
        {"month": month_abbr[m], "tenants": growth_map.get((y, m), 0)} for (y, m) in window
    ]

    status_counts: dict[str, int] = {}
    async for row in db.tenants.aggregate(
        [
            {"$match": {"isDeleted": False}},
            {"$group": {"_id": {"$toLower": "$status"}, "count": {"$sum": 1}}},
        ]
    ):
        status_counts[str(row.get("_id") or "active")] = int(row.get("count", 0))

    active = status_counts.get("active", 0)
    pending = status_counts.get("pending", 0) + status_counts.get("trial", 0)
    inactive = (
        status_counts.get("inactive", 0)
        + status_counts.get("expired", 0)
        + status_counts.get("suspended", 0)
    )
    activity = [
        {"category": "Active", "value": active, "color": "bg-green-500"},
        {"category": "Pending", "value": pending, "color": "bg-yellow-500"},
        {"category": "Inactive", "value": inactive, "color": "bg-red-500"},
    ]

    top_orgs = []
    async for row in db.tenants.aggregate(
        [
            {"$match": {"isDeleted": False}},
            {
                "$lookup": {
                    "from": "courses",
                    "localField": "_id",
                    "foreignField": "tenantId",
                    "as": "courses",
                }
            },
            {
                "$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "tenantId",
                    "as": "users",
                }
            },
            {
                "$addFields": {
                    "activeCourses": {
                        "$size": {
                            "$filter": {
                                "input": "$courses",
                                "as": "course",
                                "cond": {
                                    "$eq": [
                                        {"$toLower": {"$ifNull": ["$$course.status", "draft"]}},
                                        "published",
                                    ]
                                },
                            }
                        }
                    },
                    "usersCount": {"$size": "$users"},
                }
            },
            {
                "$project": {
                    "name": "$tenantName",
                    "activeCourses": 1,
                    "users": "$usersCount",
                }
            },
            {"$sort": {"users": -1, "activeCourses": -1, "name": 1}},
            {"$limit": top_n},
        ]
    ):
        top_orgs.append(
            {
                "name": row.get("name") or "Unknown",
                "activeCourses": int(row.get("activeCourses", 0)),
                "users": int(row.get("users", 0)),
            }
        )

    return {
        "stats": {
            "totalTenants": int(total_tenants),
            "activeUsers": int(active_users),
            "totalCourses": int(total_courses),
            "revenue": round(revenue, 2),
        },
        "tenantGrowth": tenant_growth,
        "activity": activity,
        "topOrganizations": top_orgs,
    }
