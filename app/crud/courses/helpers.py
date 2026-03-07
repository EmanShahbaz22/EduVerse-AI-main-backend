import re
from bson import ObjectId
from datetime import datetime
from typing import List, Dict, Any
from app.db.database import (
    get_courses_collection,
    get_students_collection,
    db,
    users_collection,
)


def get_collections():
    return get_courses_collection(), get_students_collection(), users_collection


def validate_id(id_str: str, label: str = "ID") -> dict | None:
    if not ObjectId.is_valid(id_str):
        return {"success": False, "message": f"Invalid {label} format: {id_str}"}
    return None


async def clean_update_data(update_dict: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {}
    for key, value in update_dict.items():
        if isinstance(value, bool):
            cleaned[key] = value
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip().lower() == "string":
            continue
        if isinstance(value, str) and value.strip() == "" and key != "thumbnailUrl":
            continue
        if isinstance(value, list):
            if len(value) > 0 and all(
                isinstance(item, dict)
                and item.get("title", "").strip().lower() == "string"
                for item in value
            ):
                continue
            if len(value) == 0 and key != "modules":
                continue
        cleaned[key] = value
    return cleaned


def get_enriched_courses_pipeline(
    query: Dict[str, Any], skip: int = 0, limit: int = 100
) -> list:
    return [
        {"$match": query},
        {
            "$addFields": {
                "teacherId": {"$toObjectId": "$teacherId"},
                "_idStr": {"$toString": "$_id"},
            }
        },
        {"$skip": skip},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "teachers",
                "localField": "teacherId",
                "foreignField": "_id",
                "as": "teacher_info",
            }
        },
        {"$unwind": {"path": "$teacher_info", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "users",
                "localField": "teacher_info.userId",
                "foreignField": "_id",
                "as": "user_info",
            }
        },
        {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "students",
                "let": {"courseIdStr": "$_idStr"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$in": [
                                    "$$courseIdStr",
                                    {
                                        "$map": {
                                            "input": {
                                                "$ifNull": ["$enrolledCourses", []]
                                            },
                                            "as": "ec",
                                            "in": {"$toString": "$$ec"},
                                        }
                                    },
                                ]
                            }
                        }
                    },
                    {"$project": {"_id": 1}},
                ],
                "as": "enrolled_students_list",
            }
        },
        {
            "$addFields": {
                "instructorName": {"$ifNull": ["$user_info.fullName", "Instructor"]},
                "enrolledStudents": {"$size": "$enrolled_students_list"},
            }
        },
        {
            "$project": {
                "teacher_info": 0,
                "user_info": 0,
                "enrolled_students_list": 0,
                "_idStr": 0,
            }
        },
    ]


def serialize_course(course: Dict[str, Any]) -> Dict[str, Any]:
    course["_id"] = str(course["_id"])
    course["tenantId"] = str(course["tenantId"])
    if "teacherId" in course and isinstance(course["teacherId"], ObjectId):
        course["teacherId"] = str(course["teacherId"])
    if not course.get("thumbnailUrl"):
        course["thumbnailUrl"] = None
    return course
