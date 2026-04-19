from bson import ObjectId

from app.db.database import db


def normalize_tenant_oid(tenant_id: str | ObjectId) -> ObjectId:
    if isinstance(tenant_id, ObjectId):
        return tenant_id
    return ObjectId(tenant_id)


async def get_tenant_course_ids(tenant_id: str | ObjectId) -> list[ObjectId]:
    tenant_oid = normalize_tenant_oid(tenant_id)
    course_docs = await db.courses.find({"tenantId": tenant_oid}, {"_id": 1}).to_list(
        length=None
    )
    return [doc["_id"] for doc in course_docs]


async def get_tenant_course_refs(tenant_id: str | ObjectId) -> list[ObjectId | str]:
    course_ids = await get_tenant_course_ids(tenant_id)
    return [*course_ids, *[str(course_id) for course_id in course_ids]]


async def get_tenant_student_query(tenant_id: str | ObjectId) -> dict:
    course_refs = await get_tenant_course_refs(tenant_id)
    if not course_refs:
        return {"_id": {"$in": []}}
    return {"enrolledCourses": {"$in": course_refs}}


async def count_tenant_students(tenant_id: str | ObjectId) -> int:
    query = await get_tenant_student_query(tenant_id)
    return await db.students.count_documents(query)


def student_has_tenant_course_membership(
    student_doc: dict, course_refs: list[ObjectId | str]
) -> bool:
    student_course_refs = {str(course_id) for course_id in student_doc.get("enrolledCourses", [])}
    tenant_course_refs = {str(course_id) for course_id in course_refs}
    return bool(student_course_refs.intersection(tenant_course_refs))
