from datetime import datetime
from bson import ObjectId
from app.db.database import db, users_collection
from app.schemas.teachers import TeacherUpdate
from app.schemas.assignments import AssignmentCreate
from app.schemas.quizzes import QuizCreate
from app.crud.quizzes import serialize_quiz
from app.crud.teachers.core import (
    to_oid,
    merge_user_data_teacher,
    serialize_teacher,
    update_teacher,
    get_teacher,
)
from app.utils.security import hash_password, verify_password
from app.utils.exceptions import not_found, bad_request


async def serialize_assignment(a: dict) -> dict:
    return {
        "id": str(a["_id"]),
        "courseId": str(a["courseId"]),
        "teacherId": str(a["teacherId"]),
        "title": a.get("title", ""),
        "description": a.get("description", ""),
        "dueDate": a.get("dueDate"),
        "dueTime": a.get("dueTime"),
        "totalMarks": a.get("totalMarks"),
        "passingMarks": a.get("passingMarks"),
        "status": a.get("status", "active"),
        "fileUrl": a.get("fileUrl", ""),
        "allowedFormats": a.get("allowedFormats", []),
        "tenantId": str(a["tenantId"]),
        "uploadedAt": a.get("uploadedAt"),
        "updatedAt": a.get("updatedAt"),
    }


async def get_teacher_assignments_route(teacher_id: str):
    cursor = db.assignments.find({"teacherId": to_oid(teacher_id, "teacherId")})
    return [serialize_assignment(a) async for a in cursor]


async def create_teacher_assignment_route(data: AssignmentCreate):
    d = data.dict()
    d["courseId"] = to_oid(d["courseId"], "courseId")
    d["teacherId"] = to_oid(d["teacherId"], "teacherId")
    d["tenantId"] = to_oid(d["tenantId"], "tenantId")
    d["uploadedAt"] = d["updatedAt"] = datetime.utcnow()
    result = await db.assignments.insert_one(d)
    return serialize_assignment(
        await db.assignments.find_one({"_id": result.inserted_id})
    )


async def get_teacher_quizzes_route(teacher_id: str):
    cursor = db.quizzes.find({"teacherId": to_oid(teacher_id, "teacherId")})
    return [serialize_quiz(q) async for q in cursor]


async def create_teacher_quiz_route(data: QuizCreate):
    d = data.dict()
    d["courseId"] = to_oid(d["courseId"], "courseId")
    d["teacherId"] = to_oid(d["teacherId"], "teacherId")
    d["tenantId"] = to_oid(d["tenantId"], "tenantId")
    d["createdAt"] = d["updatedAt"] = datetime.utcnow()
    result = await db.quizzes.insert_one(d)
    return serialize_quiz(await db.quizzes.find_one({"_id": result.inserted_id}))


async def get_teacher_dashboard(teacher_id: str):
    assignments = await get_teacher_assignments_route(teacher_id)
    quizzes = await get_teacher_quizzes_route(teacher_id)
    courses = [
        c async for c in db.courses.find({"teacherId": to_oid(teacher_id, "teacherId")})
    ]
    return {
        "totalAssignments": len(assignments),
        "totalQuizzes": len(quizzes),
        "totalCourses": len(courses),
    }


async def get_teacher_students(teacher_id: str):
    return [
        s
        async for s in db.students.find({"teacherId": to_oid(teacher_id, "teacherId")})
    ]


async def get_teacher_courses(teacher_id: str):
    cursor = db.courses.find({"teacherId": to_oid(teacher_id, "teacherId")})
    courses = []
    async for c in cursor:
        courses.append(
            {
                "id": str(c["_id"]),
                "title": c.get("title", ""),
                "description": c.get("description", ""),
                "category": c.get("category", ""),
                "status": c.get("status", ""),
                "courseCode": c.get("courseCode", ""),
                "duration": c.get("duration", ""),
                "thumbnailUrl": c.get("thumbnailUrl", ""),
                "modules": c.get("modules", []),
                "teacherId": str(c.get("teacherId", "")),
                "tenantId": str(c.get("tenantId", "")),
                "enrolledStudents": c.get("enrolledStudents", 0),
                "createdAt": c.get("createdAt"),
                "updatedAt": c.get("updatedAt"),
            }
        )
    return courses


async def get_teacher_by_user(user_id: str):
    uid = ObjectId(user_id) if isinstance(user_id, str) else user_id
    teacher = await db.teachers.find_one({"userId": uid})
    if not teacher:
        return None
    user = await users_collection.find_one({"_id": uid})
    return merge_user_data_teacher(teacher, user)


async def update_teacher_profile(user_id: str, updates: dict):
    uid = ObjectId(user_id) if isinstance(user_id, str) else user_id
    teacher = await db.teachers.find_one({"userId": uid})
    if not teacher:
        return None

    cleaned = {k: v for k, v in updates.items() if v is not None}
    cleaned["updatedAt"] = datetime.utcnow()

    user_fields = {
        "fullName",
        "email",
        "profileImageURL",
        "contactNo",
        "country",
        "status",
    }
    user_updates = {k: v for k, v in cleaned.items() if k in user_fields}
    teacher_updates = {
        k: v for k, v in cleaned.items() if k not in user_fields and k != "updatedAt"
    }

    if user_updates:
        user_updates["updatedAt"] = datetime.utcnow()
        await users_collection.update_one({"_id": uid}, {"$set": user_updates})
    if teacher_updates:
        teacher_updates["updatedAt"] = datetime.utcnow()
        await db.teachers.update_one({"userId": uid}, {"$set": teacher_updates})

    teacher = await db.teachers.find_one({"userId": uid})
    user = await users_collection.find_one({"_id": uid})
    return merge_user_data_teacher(teacher, user)


async def get_teacher_me(current_user: dict):
    teacher = await db.teachers.find_one({"userId": ObjectId(current_user["user_id"])})
    if not teacher:
        not_found("Teacher profile")
    user = await users_collection.find_one({"_id": teacher["userId"]})
    return merge_user_data_teacher(teacher, user)


async def update_teacher_me(current_user: dict, data: TeacherUpdate):
    return await update_teacher_profile(
        current_user["user_id"], data.dict(exclude_unset=True)
    )


async def change_teacher_me_password(
    current_user: dict, old_password: str, new_password: str
):
    teacher = await db.teachers.find_one({"userId": ObjectId(current_user["user_id"])})
    if not teacher:
        not_found("Teacher")
    user = await users_collection.find_one({"_id": ObjectId(teacher["userId"])})
    if not user:
        not_found("User")
    if not verify_password(old_password, user["password"]):
        bad_request("Old password is incorrect")
    if verify_password(new_password, user["password"]):
        bad_request("New password must differ from old")
    await users_collection.update_one(
        {"_id": ObjectId(teacher["userId"])},
        {
            "$set": {
                "password": hash_password(new_password),
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    await db.teachers.update_one(
        {"_id": teacher["_id"]}, {"$set": {"updatedAt": datetime.utcnow()}}
    )
    return {"message": "Password updated", "updatedAt": datetime.utcnow()}
