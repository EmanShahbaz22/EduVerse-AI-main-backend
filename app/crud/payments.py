# app/crud/payments.py
from datetime import datetime
from typing import Iterable

from app.db.database import db


def _convert_id(doc):
    """Convert MongoDB _id to string 'id' field."""
    if doc and "_id" in doc:
        doc["id"] = str(doc["_id"])
    return doc


async def create_payment(data: dict) -> dict:
    """Insert a new payment record."""
    data["createdAt"] = datetime.utcnow()
    data["updatedAt"] = datetime.utcnow()
    result = await db.payments.insert_one(data)
    doc = await db.payments.find_one({"_id": result.inserted_id})
    return _convert_id(doc)


async def find_payment_by_session(session_id: str) -> dict | None:
    """Find a payment by its Stripe session ID."""
    doc = await db.payments.find_one({"stripeSessionId": session_id})
    return _convert_id(doc)


async def find_completed_payment(
    student_id: str, course_id: str, aliases: Iterable[str] | None = None
) -> dict | None:
    """Check if a student already paid for a course."""
    student_ids = {student_id}
    if aliases:
        student_ids.update(alias for alias in aliases if alias)
    doc = await db.payments.find_one(
        {
            "studentId": {"$in": list(student_ids)},
            "courseId": course_id,
            "status": "completed",
        }
    )
    return _convert_id(doc)


async def update_payment_status(session_id: str, status: str, **extra) -> dict | None:
    """Update payment status by Stripe session ID."""
    update = {"$set": {"status": status, "updatedAt": datetime.utcnow(), **extra}}
    await db.payments.update_one({"stripeSessionId": session_id}, update)
    return await find_payment_by_session(session_id)


async def get_student_payments(
    student_id: str, aliases: Iterable[str] | None = None
) -> list[dict]:
    """Get all payments for a student."""
    student_ids = {student_id}
    if aliases:
        student_ids.update(alias for alias in aliases if alias)
    cursor = db.payments.find({"studentId": {"$in": list(student_ids)}}).sort(
        "createdAt", -1
    )
    docs = await cursor.to_list(100)
    return [_convert_id(d) for d in docs]
