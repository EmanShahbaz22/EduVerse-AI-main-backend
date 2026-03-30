from app.db.database import db
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from typing import List, Optional
from fastapi import HTTPException


def to_oid(id_str: str, field: str = "id") -> ObjectId:
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        raise HTTPException(400, f"Invalid {field}")


def serialize_submission(sub: dict) -> dict:
    def fix_date(v):
        if not v:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(v)
        except Exception:
            return v

    return {
        "id": str(sub["_id"]),
        "studentId": str(sub["studentId"]),
        "assignmentId": str(sub["assignmentId"]),
        "courseId": str(sub["courseId"]),
        "tenantId": str(sub["tenantId"]),
        "fileUrl": sub.get("fileUrl"),
        "submittedAt": fix_date(sub.get("submittedAt")),
        "obtainedMarks": sub.get("obtainedMarks"),
        "feedback": sub.get("feedback"),
        "gradedAt": fix_date(sub.get("gradedAt")),
    }


async def create_submission(data, student_id: str, tenant_id: str) -> dict:
    if not data.assignmentId or not data.courseId or not data.fileUrl:
        raise HTTPException(400, "assignmentId, courseId, and fileUrl are required")

    assignment = await db.assignments.find_one({"_id": to_oid(data.assignmentId, "assignmentId")})
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    # Use the assignment's tenantId if student has no global tenantId
    actual_tenant_id = assignment.get("tenantId")

    submission = {
        "studentId": to_oid(student_id, "studentId"),
        "assignmentId": to_oid(data.assignmentId, "assignmentId"),
        "courseId": to_oid(data.courseId, "courseId"),
        "tenantId": actual_tenant_id,
        "fileUrl": data.fileUrl,
        "submittedAt": datetime.utcnow(),
        "obtainedMarks": None,
        "feedback": None,
        "gradedAt": None,
    }
    result = await db.assignmentSubmissions.insert_one(submission)
    doc = await db.assignmentSubmissions.find_one({"_id": result.inserted_id})
    if not doc:
        raise HTTPException(500, "Failed to create submission")
    return serialize_submission(doc)


async def get_all_submissions(tenant_id: str) -> List[dict]:
    cursor = db.assignmentSubmissions.find(
        {"tenantId": to_oid(tenant_id, "tenantId")}
    ).sort("submittedAt", -1)
    return [serialize_submission(s) async for s in cursor]


async def get_submissions_by_student(student_id: str, tenant_id: str) -> List[dict]:
    query = {"studentId": to_oid(student_id, "studentId")}
    if tenant_id and ObjectId.is_valid(tenant_id):
        query["tenantId"] = to_oid(tenant_id, "tenantId")

    cursor = db.assignmentSubmissions.find(query).sort("submittedAt", -1)
    return [serialize_submission(s) async for s in cursor]


async def get_submissions_by_assignment(
    assignment_id: str, tenant_id: str
) -> List[dict]:
    cursor = db.assignmentSubmissions.find(
        {
            "assignmentId": to_oid(assignment_id, "assignmentId"),
            "tenantId": to_oid(tenant_id, "tenantId"),
        }
    ).sort("submittedAt", -1)
    return [serialize_submission(s) async for s in cursor]


async def grade_submission(
    submission_id: str,
    tenant_id: str,
    marks: Optional[int] = None,
    feedback: Optional[str] = None,
) -> dict:
    if marks is None and feedback is None:
        raise HTTPException(400, "Nothing to update")
    updates = {"gradedAt": datetime.utcnow()}
    if marks is not None:
        updates["obtainedMarks"] = marks
    if feedback is not None:
        updates["feedback"] = feedback
    q = {
        "_id": to_oid(submission_id, "submissionId"),
        "tenantId": to_oid(tenant_id, "tenantId"),
    }
    if (
        await db.assignmentSubmissions.update_one(q, {"$set": updates})
    ).matched_count == 0:
        raise HTTPException(404, "Submission not found")
    return serialize_submission(await db.assignmentSubmissions.find_one(q))


async def delete_submission(submission_id: str, tenant_id: str) -> bool:
    q = {
        "_id": to_oid(submission_id, "submissionId"),
        "tenantId": to_oid(tenant_id, "tenantId"),
    }
    return (await db.assignmentSubmissions.delete_one(q)).deleted_count > 0
