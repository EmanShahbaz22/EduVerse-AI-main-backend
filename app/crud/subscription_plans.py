from datetime import datetime
from typing import Optional
import re

from bson import ObjectId
from fastapi import HTTPException, status

from app.core.settings import MAX_SUBSCRIPTION_PLANS
from app.db.database import db


def _ensure_objectid(raw_id: str) -> ObjectId:
    if not ObjectId.is_valid(raw_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid subscription plan id",
        )
    return ObjectId(raw_id)


def _normalize_code(code: str) -> str:
    return "-".join(code.strip().lower().split())


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_features(values: Optional[list[str]]) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def _serialize_plan(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "code": doc["code"],
        "name": doc["name"],
        "category": doc.get("category", "custom"),
        "billingCycle": doc.get("billingCycle", "monthly"),
        "pricePerMonth": float(doc.get("pricePerMonth", 0)),
        "maxStudents": doc.get("maxStudents"),
        "maxTeachers": doc.get("maxTeachers"),
        "maxCourses": doc.get("maxCourses"),
        "aiCredits": doc.get("aiCredits"),
        "storageGb": doc.get("storageGb"),
        "description": doc.get("description"),
        "features": doc.get("features", []),
        "status": doc.get("status", "active"),
        "createdAt": doc["createdAt"],
        "updatedAt": doc.get("updatedAt"),
    }


async def list_subscription_plans(status_filter: Optional[str] = None) -> list[dict]:
    query: dict = {"isDeleted": False}
    if status_filter:
        query["status"] = status_filter

    cursor = db.subscriptionPlans.find(query).sort("createdAt", -1)
    docs = await cursor.to_list(length=500)
    return [_serialize_plan(doc) for doc in docs]


async def get_subscription_plan(plan_id: str) -> Optional[dict]:
    doc = await db.subscriptionPlans.find_one(
        {"_id": _ensure_objectid(plan_id), "isDeleted": False}
    )
    return _serialize_plan(doc) if doc else None


async def create_subscription_plan(data: dict) -> dict:
    existing_plan_count = await db.subscriptionPlans.count_documents(
        {"isDeleted": False}
    )
    if existing_plan_count >= MAX_SUBSCRIPTION_PLANS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only {MAX_SUBSCRIPTION_PLANS} subscription plans are allowed.",
        )

    normalized = {
        "code": _normalize_code(data["code"]),
        "name": data["name"].strip(),
        "category": data.get("category", "custom").strip().lower(),
        "billingCycle": data.get("billingCycle", "monthly"),
        "pricePerMonth": float(data.get("pricePerMonth", 0)),
        "maxStudents": data.get("maxStudents"),
        "maxTeachers": data.get("maxTeachers"),
        "maxCourses": data.get("maxCourses"),
        "aiCredits": data.get("aiCredits"),
        "storageGb": data.get("storageGb"),
        "description": _normalize_text(data.get("description")),
        "features": _normalize_features(data.get("features")),
        "status": data.get("status", "active"),
        "createdAt": datetime.utcnow(),
        "updatedAt": None,
        "isDeleted": False,
    }

    existing = await db.subscriptionPlans.find_one(
        {"code": normalized["code"], "isDeleted": False}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A plan with this code already exists",
        )

    result = await db.subscriptionPlans.insert_one(normalized)
    created = await db.subscriptionPlans.find_one({"_id": result.inserted_id})
    return _serialize_plan(created)


async def update_subscription_plan(plan_id: str, updates: dict) -> Optional[dict]:
    object_id = _ensure_objectid(plan_id)
    safe_updates = dict(updates)

    if "code" in safe_updates and safe_updates["code"] is not None:
        safe_updates["code"] = _normalize_code(safe_updates["code"])
        existing = await db.subscriptionPlans.find_one(
            {
                "code": safe_updates["code"],
                "isDeleted": False,
                "_id": {"$ne": object_id},
            }
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A plan with this code already exists",
            )

    if "name" in safe_updates and safe_updates["name"] is not None:
        safe_updates["name"] = safe_updates["name"].strip()
    if "category" in safe_updates and safe_updates["category"] is not None:
        safe_updates["category"] = safe_updates["category"].strip().lower()
    if "description" in safe_updates:
        safe_updates["description"] = _normalize_text(safe_updates.get("description"))
    if "features" in safe_updates:
        safe_updates["features"] = _normalize_features(safe_updates.get("features"))
    if "pricePerMonth" in safe_updates and safe_updates["pricePerMonth"] is not None:
        safe_updates["pricePerMonth"] = float(safe_updates["pricePerMonth"])

    safe_updates["updatedAt"] = datetime.utcnow()

    result = await db.subscriptionPlans.update_one(
        {"_id": object_id, "isDeleted": False},
        {"$set": safe_updates},
    )
    if result.matched_count == 0:
        return None

    updated = await db.subscriptionPlans.find_one(
        {"_id": object_id, "isDeleted": False}
    )
    return _serialize_plan(updated) if updated else None


async def delete_subscription_plan(plan_id: str) -> bool:
    object_id = _ensure_objectid(plan_id)

    plan_doc = await db.subscriptionPlans.find_one({"_id": object_id, "isDeleted": False})
    if not plan_doc:
        return False

    plan_code = str(plan_doc.get("code", "")).strip()
    plan_name = str(plan_doc.get("name", "")).strip()

    plan_refs: list[dict] = [{"subscriptionId": object_id}]
    if plan_code:
        plan_refs.append(
            {
                "subscriptionPlan": {
                    "$regex": f"^{re.escape(plan_code)}$",
                    "$options": "i",
                }
            }
        )
    if plan_name:
        plan_refs.append(
            {
                "subscriptionPlan": {
                    "$regex": f"^{re.escape(plan_name)}$",
                    "$options": "i",
                }
            }
        )

    in_use = await db.tenants.count_documents(
        {
            "isDeleted": {"$ne": True},
            "$or": plan_refs,
        }
    )
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Cannot delete plan "{plan_doc.get("name", plan_code)}" because it is '
                f"assigned to {in_use} tenant(s). Reassign those tenants first."
            ),
        )

    result = await db.subscriptionPlans.update_one(
        {"_id": object_id, "isDeleted": False},
        {
            "$set": {
                "isDeleted": True,
                "status": "inactive",
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    return result.modified_count > 0
