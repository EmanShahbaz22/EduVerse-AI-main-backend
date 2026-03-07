from bson import ObjectId
from datetime import datetime
from typing import List

from app.crud.courses.helpers import get_collections, serialize_course, validate_id

courses_col, students_col, _ = get_collections()


async def _verify_tenant_entity(collection, entity_id: str, tenant_id: str, label: str):
    """Verifies entity exists within tenant. Returns (entity, error_dict)."""
    oid, toid = ObjectId(entity_id), ObjectId(tenant_id)
    doc = await collection.find_one({"_id": oid, "tenantId": toid})
    if doc:
        return doc, None
    if await collection.find_one({"_id": oid}):
        return None, {
            "success": False,
            "message": f"{label} belongs to different tenant",
        }
    return None, {"success": False, "message": f"{label} not found: {entity_id}"}


async def _get_course_for_enrollment(
    course_id: str, tenant_id: str | None, enforce_same_tenant: bool
):
    if enforce_same_tenant:
        if not tenant_id:
            return None, {"success": False, "message": "Tenant context required"}
        return await _verify_tenant_entity(courses_col, course_id, tenant_id, "Course")

    oid = ObjectId(course_id)
    course = await courses_col.find_one({"_id": oid})
    if not course:
        return None, {"success": False, "message": f"Course not found: {course_id}"}
    return course, None


async def _get_student_for_enrollment(
    student_id: str, tenant_id: str | None, enforce_same_tenant: bool
):
    if enforce_same_tenant:
        if not tenant_id:
            return None, {"success": False, "message": "Tenant context required"}
        return await _verify_tenant_entity(
            students_col, student_id, tenant_id, "Student"
        )

    student = await students_col.find_one({"_id": ObjectId(student_id)})
    if not student:
        return None, {"success": False, "message": f"Student not found: {student_id}"}
    return student, None


async def enroll_student(
    course_id: str,
    student_id: str,
    tenant_id: str | None,
    *,
    enforce_same_tenant: bool = True,
) -> dict:
    for id_val, label in [
        (course_id, "course"),
        (student_id, "student"),
    ]:
        err = validate_id(id_val, label)
        if err:
            return err
    if tenant_id:
        err = validate_id(tenant_id, "tenant")
        if err:
            return err
    if enforce_same_tenant and not tenant_id:
        return {"success": False, "message": "Tenant context required"}

    course, err = await _get_course_for_enrollment(
        course_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err
    student, err = await _get_student_for_enrollment(
        student_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err

    if course_id in student.get("enrolledCourses", []):
        return {"success": False, "message": "Already enrolled"}

    student_filter = {"_id": ObjectId(student_id)}
    if enforce_same_tenant and tenant_id:
        student_filter["tenantId"] = ObjectId(tenant_id)

    await students_col.update_one(
        student_filter,
        {
            "$addToSet": {"enrolledCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )
    await courses_col.update_one(
        {"_id": course["_id"]},
        {"$inc": {"enrolledStudents": 1}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return {"success": True, "message": "Enrolled successfully"}


async def unenroll_student(
    course_id: str,
    student_id: str,
    tenant_id: str | None,
    *,
    enforce_same_tenant: bool = True,
) -> dict:
    for id_val, label in [
        (course_id, "course"),
        (student_id, "student"),
    ]:
        err = validate_id(id_val, label)
        if err:
            return err
    if tenant_id:
        err = validate_id(tenant_id, "tenant")
        if err:
            return err
    if enforce_same_tenant and not tenant_id:
        return {"success": False, "message": "Tenant context required"}

    course, err = await _get_course_for_enrollment(
        course_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err
    student, err = await _get_student_for_enrollment(
        student_id, tenant_id, enforce_same_tenant
    )
    if err:
        return err

    if course_id not in student.get("enrolledCourses", []):
        return {"success": False, "message": "Not enrolled in this course"}

    student_filter = {"_id": ObjectId(student_id)}
    if enforce_same_tenant and tenant_id:
        student_filter["tenantId"] = ObjectId(tenant_id)

    await students_col.update_one(
        student_filter,
        {
            "$pull": {"enrolledCourses": course_id},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )
    await courses_col.update_one(
        {"_id": course["_id"]},
        {"$inc": {"enrolledStudents": -1}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return {"success": True, "message": "Unenrolled successfully"}


async def reorder_lessons(
    course_id: str, tenant_id: str, module_id: str, lesson_ids: List[str]
) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    modules = course.get("modules", [])
    found = False
    for mod in modules:
        if mod.get("id") == module_id:
            found = True
            lesson_map = {l.get("id"): l for l in mod.get("lessons", [])}
            reordered = [
                dict(lesson_map[lid], order=i)
                for i, lid in enumerate(lesson_ids)
                if lid in lesson_map
            ]
            for l in mod.get("lessons", []):
                if l.get("id") not in lesson_ids:
                    l["order"] = len(reordered)
                    reordered.append(l)
            mod["lessons"] = reordered
            break

    if not found:
        return {"success": False, "message": f"Module not found: {module_id}"}

    res = await courses_col.update_one(
        {"_id": ObjectId(course_id)},
        {"$set": {"modules": modules, "updatedAt": datetime.now()}},
    )
    if res.modified_count == 0:
        return {"success": False, "message": "Failed to reorder"}

    updated = await courses_col.find_one({"_id": ObjectId(course_id)})
    return {
        "success": True,
        "message": "Lessons reordered",
        "course": serialize_course(updated),
    }


async def reorder_modules(
    course_id: str, tenant_id: str, module_ids: List[str]
) -> dict:
    err = validate_id(course_id, "course") or validate_id(tenant_id, "tenant")
    if err:
        return err

    course = await courses_col.find_one(
        {"_id": ObjectId(course_id), "tenantId": ObjectId(tenant_id)}
    )
    if not course:
        return {"success": False, "message": "Course not found or wrong tenant"}

    mod_map = {m.get("id"): m for m in course.get("modules", [])}
    reordered = [
        dict(mod_map[mid], order=i)
        for i, mid in enumerate(module_ids)
        if mid in mod_map
    ]
    for m in course.get("modules", []):
        if m.get("id") not in module_ids:
            m["order"] = len(reordered)
            reordered.append(m)

    res = await courses_col.update_one(
        {"_id": ObjectId(course_id)},
        {"$set": {"modules": reordered, "updatedAt": datetime.now()}},
    )
    if res.modified_count == 0:
        return {"success": False, "message": "Failed to reorder"}

    updated = await courses_col.find_one({"_id": ObjectId(course_id)})
    return {
        "success": True,
        "message": "Modules reordered",
        "course": serialize_course(updated),
    }
