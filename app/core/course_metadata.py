from typing import Iterable

from bson import ObjectId

from app.db.database import db

DEFAULT_COURSE_CATEGORY = "General"
DEFAULT_COURSE_LEVEL = "Beginner"

SYSTEM_COURSE_CATEGORIES = (
    DEFAULT_COURSE_CATEGORY,
    "Computer Science",
    "Technology",
    "Mathematics",
    "Science",
    "Business",
    "Arts",
    "Language",
    "Health",
    "Engineering",
    "History",
    "Other",
)

COURSE_LEVELS = (
    DEFAULT_COURSE_LEVEL,
    "Intermediate",
    "Advanced",
)


def normalize_course_category(value: str) -> str:
    return " ".join((value or "").split()).strip()


def normalize_custom_categories(values: Iterable[str] | None) -> list[str]:
    normalized_categories: list[str] = []
    seen: set[str] = set()
    system_keys = {category.casefold() for category in SYSTEM_COURSE_CATEGORIES}

    for value in values or []:
        normalized_value = normalize_course_category(value)
        if not normalized_value:
            continue
        if len(normalized_value) > 60:
            raise ValueError("Course category names must be 60 characters or fewer")

        normalized_key = normalized_value.casefold()
        if normalized_key in system_keys or normalized_key in seen:
            continue

        seen.add(normalized_key)
        normalized_categories.append(normalized_value)

    return normalized_categories


def merge_course_categories(*groups: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in groups:
        for value in group:
            normalized_value = normalize_course_category(value)
            if not normalized_value:
                continue

            normalized_key = normalized_value.casefold()
            if normalized_key in seen:
                continue

            seen.add(normalized_key)
            merged.append(normalized_value)

    return merged


async def get_tenant_custom_categories(tenant_id: str | None) -> list[str]:
    if not tenant_id or not ObjectId.is_valid(tenant_id):
        return []

    tenant = await db.tenants.find_one(
        {"_id": ObjectId(tenant_id), "isDeleted": False},
        {"courseCategories": 1},
    )
    if not tenant:
        return []

    return normalize_custom_categories(tenant.get("courseCategories", []))


async def get_tenant_used_categories(tenant_id: str | None) -> list[str]:
    if not tenant_id or not ObjectId.is_valid(tenant_id):
        return []

    used_categories = await db.courses.distinct(
        "category",
        {"tenantId": ObjectId(tenant_id)},
    )

    return merge_course_categories(used_categories)


async def get_course_metadata(tenant_id: str | None = None) -> dict:
    custom_categories = await get_tenant_custom_categories(tenant_id)
    used_categories = await get_tenant_used_categories(tenant_id)
    categories = merge_course_categories(
        SYSTEM_COURSE_CATEGORIES,
        custom_categories,
        used_categories,
    )

    return {
        "categories": categories,
        "levels": list(COURSE_LEVELS),
        "defaultCategory": DEFAULT_COURSE_CATEGORY,
        "defaultLevel": DEFAULT_COURSE_LEVEL,
        "systemCategories": list(SYSTEM_COURSE_CATEGORIES),
        "customCategories": custom_categories,
    }


async def update_tenant_custom_categories(
    tenant_id: str,
    categories: Iterable[str] | None,
) -> dict:
    if not ObjectId.is_valid(tenant_id):
        raise ValueError("Invalid tenant ID")

    tenant_oid = ObjectId(tenant_id)
    tenant = await db.tenants.find_one({"_id": tenant_oid, "isDeleted": False}, {"_id": 1})
    if not tenant:
        raise ValueError("Tenant not found")

    normalized_custom_categories = normalize_custom_categories(categories)
    allowed_after_update = {
        category.casefold()
        for category in merge_course_categories(
            SYSTEM_COURSE_CATEGORIES,
            normalized_custom_categories,
        )
    }
    used_categories = await get_tenant_used_categories(tenant_id)
    missing_used_categories = [
        category
        for category in used_categories
        if category.casefold() not in allowed_after_update
    ]
    if missing_used_categories:
        raise ValueError(
            "Cannot remove categories that are already used by courses: "
            + ", ".join(missing_used_categories)
        )

    update_operation = (
        {"$set": {"courseCategories": normalized_custom_categories}}
        if normalized_custom_categories
        else {"$unset": {"courseCategories": ""}}
    )
    update_operation.setdefault("$set", {})
    update_operation["$set"]["updatedAt"] = __import__("datetime").datetime.utcnow()

    await db.tenants.update_one({"_id": tenant_oid, "isDeleted": False}, update_operation)
    return await get_course_metadata(tenant_id)


async def ensure_course_category_allowed(tenant_id: str, category: str) -> str:
    normalized_category = normalize_course_category(category)
    metadata = await get_course_metadata(tenant_id)
    allowed_categories = {value.casefold() for value in metadata["categories"]}

    if normalized_category.casefold() not in allowed_categories:
        raise ValueError(
            "Invalid category. Allowed values: " + ", ".join(metadata["categories"])
        )

    return normalized_category


def validate_course_level(value: str) -> str:
    normalized = value.strip()
    if normalized not in COURSE_LEVELS:
        raise ValueError(f"Invalid level. Allowed values: {', '.join(COURSE_LEVELS)}")
    return normalized
