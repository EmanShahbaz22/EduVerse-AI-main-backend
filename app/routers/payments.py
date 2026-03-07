# app/routers/payments.py
import asyncio
import os

import stripe
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Depends, Request

from app.auth.dependencies import require_role
from app.schemas.payments import CheckoutRequest, PaymentResponse
from app.crud.payments import (
    create_payment,
    find_payment_by_session,
    find_completed_payment,
    update_payment_status,
    get_student_payments,
)
from app.crud.courses import course_crud
from app.db.database import db
from dotenv import load_dotenv
from typing import List

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:4200")

router = APIRouter(prefix="/payments", tags=["Payments"])


def _ensure_objectid(value: str, field_name: str):
    if not value or not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return ObjectId(value)


async def _get_current_student_profile(current_user: dict) -> dict:
    user_oid = _ensure_objectid(current_user["user_id"], "user id")
    student = await db.students.find_one({"userId": user_oid})
    if not student:
        raise HTTPException(status_code=403, detail="Student profile not found")
    if not student.get("tenantId"):
        raise HTTPException(status_code=403, detail="Tenant context missing for student")
    return {"student_id": str(student["_id"]), "tenant_id": str(student["tenantId"])}


async def _resolve_student_profile(student_or_user_id: str, tenant_hint: str | None = None):
    if not student_or_user_id or not ObjectId.is_valid(student_or_user_id):
        return None
    oid = ObjectId(student_or_user_id)
    student = await db.students.find_one({"_id": oid})
    if not student:
        student = await db.students.find_one({"userId": oid})
    if not student:
        return None
    tenant_id = str(student["tenantId"]) if student.get("tenantId") else tenant_hint
    if not tenant_id:
        return None
    return {"student_id": str(student["_id"]), "tenant_id": tenant_id}


async def _get_course_by_id_global(course_id: str) -> dict:
    course_oid = _ensure_objectid(course_id, "courseId")
    course = await db.courses.find_one({"_id": course_oid})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


# ─── 0. Public Config (returns publishable key to frontend) ──────────


@router.get("/config")
async def get_stripe_config():
    """Returns the Stripe publishable key for frontend initialization."""
    return {"publishableKey": STRIPE_PUBLISHABLE_KEY}


# ─── 1. Create Payment Intent (embedded checkout) ────────────────────


@router.post("/create-payment-intent")
async def create_payment_intent(
    data: CheckoutRequest,
    current_user=Depends(require_role("student")),
):
    """
    Creates a Stripe PaymentIntent for a paid course.
    Returns a clientSecret that the frontend uses with Stripe Elements.
    """
    student_profile = await _get_current_student_profile(current_user)
    student_id = student_profile["student_id"]
    tenant_id = student_profile["tenant_id"]

    # 1. Fetch the course globally (marketplace allows cross-tenant enrollment).
    course = await _get_course_by_id_global(data.courseId)

    # 2. Only paid courses need payment
    if course.get("isFree", True):
        raise HTTPException(
            status_code=400, detail="This course is free, no payment needed"
        )

    price = course.get("price", 0)
    if price <= 0:
        raise HTTPException(status_code=400, detail="Course has no valid price")

    # 3. Prevent double payment
    existing = await find_completed_payment(
        student_id, data.courseId, aliases=[current_user["user_id"]]
    )
    if existing:
        raise HTTPException(
            status_code=400, detail="You have already paid for this course"
        )

    # 4. Create Stripe PaymentIntent
    try:
        intent = await asyncio.to_thread(
            stripe.PaymentIntent.create,
            amount=int(price * 100),
            currency=course.get("currency", "usd").lower(),
            metadata={
                "courseId": data.courseId,
                "studentId": student_id,
                "tenantId": tenant_id,
            },
        )
    except stripe.error.StripeError:
        raise HTTPException(status_code=500, detail="Payment processing failed")

    # 5. Save a pending payment record
    await create_payment(
        {
            "courseId": data.courseId,
            "studentId": student_id,
            "tenantId": tenant_id,
            "amount": price,
            "currency": course.get("currency", "USD"),
            "status": "pending",
            "stripeSessionId": intent.id,  # Store PaymentIntent ID
        }
    )

    return {"clientSecret": intent.client_secret}


# ─── 2. Stripe Webhook (called by Stripe, not by frontend) ───────────


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Handles Stripe webhook events.
    On successful payment: marks payment as completed + enrolls student.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Verify the webhook signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle payment success
    if event["type"] == "payment_intent.succeeded":
        intent = event["data"]["object"]
        intent_id = intent["id"]
        metadata = intent.get("metadata", {})

        course_id = metadata.get("courseId")
        student_id = metadata.get("studentId")
        tenant_id = metadata.get("tenantId")

        # Idempotency: skip if already processed
        existing = await find_payment_by_session(intent_id)
        if existing and existing.get("status") == "completed":
            return {"status": "already processed"}

        # Mark payment as completed
        await update_payment_status(intent_id, "completed")

        # Auto-enroll the student
        if course_id and student_id:
            resolved = await _resolve_student_profile(student_id, tenant_id)
            if resolved:
                await update_payment_status(
                    intent_id,
                    "completed",
                    studentId=resolved["student_id"],
                    tenantId=resolved["tenant_id"],
                )
                enrollment = await course_crud.enroll_student(
                    course_id,
                    resolved["student_id"],
                    resolved["tenant_id"],
                    enforce_same_tenant=False,
                )
                if not enrollment.get("success") and enrollment.get("message") != "Already enrolled":
                    await update_payment_status(
                        intent_id, "completed", enrollmentError=enrollment.get("message")
                    )

    elif event["type"] == "payment_intent.payment_failed":
        intent = event["data"]["object"]
        await update_payment_status(intent["id"], "failed")

    return {"status": "ok"}


# ─── 3. Confirm Payment (frontend calls after successful card payment) ──


@router.post("/confirm/{payment_intent_id}")
async def confirm_payment(
    payment_intent_id: str,
    current_user=Depends(require_role("student")),
):
    """
    Called by frontend after Stripe Elements confirms payment client-side.
    Enrolls the student if payment is verified as succeeded.
    """
    # Verify with Stripe that this payment actually succeeded
    try:
        intent = await asyncio.to_thread(
            stripe.PaymentIntent.retrieve, payment_intent_id
        )
    except stripe.error.StripeError:
        raise HTTPException(status_code=400, detail="Invalid payment")

    if intent.status != "succeeded":
        raise HTTPException(status_code=400, detail="Payment not completed")

    metadata = intent.get("metadata", {})
    course_id = metadata.get("courseId")
    student_id = metadata.get("studentId")
    tenant_id = metadata.get("tenantId")
    student_profile = await _get_current_student_profile(current_user)
    resolved_student = await _resolve_student_profile(student_id, tenant_id)
    if not resolved_student:
        raise HTTPException(status_code=403, detail="Invalid payment ownership metadata")

    # Verify the student matches
    if resolved_student["student_id"] != student_profile["student_id"]:
        raise HTTPException(status_code=403, detail="Not your payment")

    # Mark as completed + enroll (idempotent)
    existing = await find_payment_by_session(payment_intent_id)
    if existing and existing.get("status") != "completed":
        await update_payment_status(
            payment_intent_id,
            "completed",
            studentId=student_profile["student_id"],
            tenantId=student_profile["tenant_id"],
        )

    enrollment = await course_crud.enroll_student(
        course_id,
        student_profile["student_id"],
        student_profile["tenant_id"],
        enforce_same_tenant=False,
    )
    if not enrollment.get("success") and enrollment.get("message") != "Already enrolled":
        raise HTTPException(status_code=400, detail=enrollment.get("message"))

    return {"status": "success", "courseId": course_id}


# ─── 4. Payment History ─────────────────────────────────────────────


@router.get("/my-payments", response_model=List[PaymentResponse])
async def my_payments(current_user=Depends(require_role("student"))):
    """Get all payments for the current student."""
    student_profile = await _get_current_student_profile(current_user)
    return await get_student_payments(
        student_profile["student_id"], aliases=[current_user["user_id"]]
    )
