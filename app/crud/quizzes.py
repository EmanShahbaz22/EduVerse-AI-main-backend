from typing import Optional, Any
from bson import ObjectId
from datetime import datetime
from fastapi import HTTPException, status
from app.db.database import db


def _ensure_objectid(_id: str, name: str = "id"):
    if not ObjectId.is_valid(_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid ObjectId for {name}")
    return ObjectId(_id)


def serialize_quiz(quiz: dict) -> dict:
    return {
        "id": str(quiz["_id"]),
        "courseId": str(quiz["courseId"]),
        "courseName": str(quiz["courseName"]),
        "teacherId": str(quiz["teacherId"]),
        "tenantId": str(quiz["tenantId"]),
        "quizNumber": quiz["quizNumber"],
        "description": quiz.get("description"),
        "dueDate": quiz["dueDate"],
        "questions": quiz["questions"],
        "timeLimitMinutes": quiz.get("timeLimitMinutes"),
        "totalMarks": quiz["totalMarks"],
        "aiGenerated": quiz.get("aiGenerated", False),
        "status": quiz.get("status", "active"),
        "createdAt": quiz["createdAt"],
        "updatedAt": quiz.get("updatedAt"),
    }


async def create_quiz(request):
    data = request.dict()
    data["courseId"] = _ensure_objectid(data["courseId"], "courseId")
    data["teacherId"] = _ensure_objectid(data["teacherId"], "teacherId")
    data["tenantId"] = _ensure_objectid(data["tenantId"], "tenantId")
    data.update(
        {
            "status": "active",
            "createdAt": datetime.utcnow(),
            "updatedAt": None,
            "isDeleted": False,
            "deletedAt": None,
        }
    )
    res = await db.quizzes.insert_one(data)
    return serialize_quiz(await db.quizzes.find_one({"_id": res.inserted_id}))


async def get_quiz(_id: str):
    _id = _ensure_objectid(_id, "quizId")
    quiz = await db.quizzes.find_one({"_id": _id, "isDeleted": False})
    return serialize_quiz(quiz) if quiz else None


async def get_quizzes_filtered(
    tenantId: Optional[str] = None,
    teacherId: Optional[str] = None,
    courseId: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = "createdAt",
    page: int = 1,
    limit: int = 10,
):
    query: dict[str, Any] = {"isDeleted": False}
    if tenantId:
        query["tenantId"] = ObjectId(tenantId)
    if teacherId:
        query["teacherId"] = ObjectId(teacherId)
    if courseId:
        query["courseId"] = ObjectId(courseId)
    if search:
        query["description"] = {"$regex": search, "$options": "i"}

    sort_dir = -1 if sort.startswith("-") else 1
    cursor = (
        db.quizzes.find(query)
        .sort(sort.lstrip("-"), sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    return [serialize_quiz(q) async for q in cursor]


async def update_quiz(_id: str, teacherId: str, updates: dict):
    """Update quiz; blocks question changes if submissions exist."""
    _ensure_objectid(_id, "quizId")
    quiz = await db.quizzes.find_one({"_id": ObjectId(_id), "isDeleted": False})
    if not quiz:
        return None
    if str(quiz["teacherId"]) != str(teacherId):
        return "Unauthorized"

    has_submissions = (
        await db.quizSubmissions.count_documents({"quizId": ObjectId(_id)}) > 0
    )
    restricted = {"questions", "totalMarks", "quizNumber"}

    safe = {
        k: v
        for k, v in updates.items()
        if v is not None and v != "" and not (has_submissions and k in restricted)
    }
    safe["updatedAt"] = datetime.utcnow()
    await db.quizzes.update_one({"_id": ObjectId(_id)}, {"$set": safe})
    return serialize_quiz(await db.quizzes.find_one({"_id": ObjectId(_id)}))


async def delete_quiz(_id, teacherId):
    quiz = await db.quizzes.find_one({"_id": ObjectId(_id), "isDeleted": False})
    if not quiz:
        return None
    if str(quiz["teacherId"]) != str(teacherId):
        return "Unauthorized"
    await db.quizzes.update_one(
        {"_id": ObjectId(_id)},
        {
            "$set": {
                "isDeleted": True,
                "deletedAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    return True


async def has_quiz_submissions(quiz_id: str) -> bool:
    _ensure_objectid(quiz_id, "quizId")
    return await db.quizSubmissions.count_documents({"quizId": ObjectId(quiz_id)}) > 0


async def get_student_quizzes(user_id: str, tenant_id: str | None = None):
    user_oid = _ensure_objectid(user_id, "userId")
    tenant_oid = _ensure_objectid(tenant_id, "tenantId") if tenant_id else None
    student = await db.students.find_one({"userId": user_oid})
    if not student:
        return []
    course_ids = [
        ObjectId(c) for c in student.get("enrolledCourses", []) if ObjectId.is_valid(c)
    ]
    if not course_ids:
        return []
    query: dict[str, Any] = {
        "courseId": {"$in": course_ids},
        "isDeleted": False,
        "status": "active",
    }
    if tenant_oid:
        query["tenantId"] = tenant_oid

    cursor = db.quizzes.find(query).sort("createdAt", -1)
    return [serialize_quiz(q) async for q in cursor]
