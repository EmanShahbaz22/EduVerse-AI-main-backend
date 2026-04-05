from bson import ObjectId

from app.db.database import db
from app.utils.tenant_students import get_tenant_student_query


def convert_objectids(doc):
    """
    Recursively convert ObjectId fields in a dict or list to strings.
    """
    if isinstance(doc, list):
        return [convert_objectids(d) for d in doc]
    if isinstance(doc, dict):
        return {k: convert_objectids(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc


def _to_objectid(value):
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


async def get_all_students(tenant_id: str):
    students = []

    if not tenant_id or not ObjectId.is_valid(tenant_id):
        return []

    tenant_oid = ObjectId(tenant_id)

    query = await get_tenant_student_query(tenant_oid)

    async for s in db.students.find(query):
        user_oid = _to_objectid(s.get("userId"))
        if not user_oid:
            continue
        user = await db.users.find_one({"_id": user_oid})

        if not user:
            continue

        student_data = {
            "id": s["_id"],
            "fullName": user.get("fullName", ""),
            "email": user.get("email", ""),
            "status": user.get("status", "active"),
            "country": user.get("country"),
            "enrolledCourses": s.get("enrolledCourses", []),
            "completedCourses": s.get("completedCourses", []),
            "tenantId": None,
            "userId": s.get("userId"),
        }

        # Convert all ObjectId fields to strings
        students.append(convert_objectids(student_data))

    return students


async def get_all_teachers(tenant_id: str):
    teachers = []

    if not tenant_id or not ObjectId.is_valid(tenant_id):
        return []

    tenant_oid = ObjectId(tenant_id)
    async for t in db.teachers.find({"tenantId": tenant_oid}):
        user_oid = _to_objectid(t.get("userId"))
        if not user_oid:
            continue
        user = await db.users.find_one({"_id": user_oid})

        if not user:
            continue

        # Merge user data directly into teacher object
        teachers.append(
            {
                "id": str(t["_id"]),
                "fullName": user.get("fullName", ""),
                "email": user.get("email", ""),
                "status": user.get("status", "active"),
                "role": user.get("role", "teacher"),
                "contactNo": user.get("contactNo", ""),
                "country": user.get("country", ""),
                "assignedCourses": [str(c) for c in t.get("assignedCourses", [])],
                "qualifications": t.get("qualifications", []),
                "subjects": t.get("subjects", []),
            }
        )

    return teachers


async def get_all_courses(tenant_id: str):
    courses = []

    if not tenant_id or not ObjectId.is_valid(tenant_id):
        return []

    tenant_oid = ObjectId(tenant_id)
    async for c in db.courses.find({"tenantId": tenant_oid}):
        teacher_id = c.get("teacherId")
        teacher_str = str(teacher_id) if teacher_id else ""
        tenant_str = str(c.get("tenantId", ""))
        courses.append(
            {
                "id": str(c["_id"]),
                "title": c.get("title", ""),
                "courseCode": c.get("courseCode", ""),
                "description": c.get("description", ""),
                "category": c.get("category", ""),
                "status": c.get("status", ""),
                "duration": c.get("duration", ""),
                "enrolledStudents": c.get("enrolledStudents", 0),
                "teacherId": teacher_str,
                "tenantId": tenant_str,
            }
        )

    return courses
