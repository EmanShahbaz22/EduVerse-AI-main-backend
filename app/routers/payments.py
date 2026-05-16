# app/routers/payments.py
import asyncio
import os
import logging

from datetime import datetime

from datetime import datetime

import stripe
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Depends, Request

# Initialize logger
logger = logging.getLogger(__name__)

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
from app.core.settings import FRONTEND_URL
from app.db.database import db
from dotenv import load_dotenv
from typing import List

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

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
    return {
        "student_id": str(student["_id"]),
        "tenant_id": None,
    }


async def _resolve_student_profile(student_or_user_id: str, tenant_hint: str | None = None):
    if not student_or_user_id or not ObjectId.is_valid(student_or_user_id):
        return None
    oid = ObjectId(student_or_user_id)
    student = await db.students.find_one({"_id": oid})
    if not student:
        student = await db.students.find_one({"userId": oid})
    if not student:
        return None
    tenant_id = tenant_hint
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
    Creates a Stripe Embedded Checkout session for a paid course.
    Returns a Checkout clientSecret that the frontend uses with embedded Checkout.
    """
    student_profile = await _get_current_student_profile(current_user)
    student_id = student_profile["student_id"]

    # 1. Fetch the course globally (marketplace allows cross-tenant enrollment).
    course = await _get_course_by_id_global(data.courseId)
    tenant_id = student_profile["tenant_id"] or (
        str(course["tenantId"]) if course.get("tenantId") else None
    )
    if not tenant_id:
        raise HTTPException(
            status_code=400, detail="Unable to determine tenant context for this course"
        )

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

    # 4. Create Stripe Embedded Checkout session
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            ui_mode="embedded_page",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": course.get("currency", "usd").lower(),
                        "product_data": {
                            "name": course.get("title", "Course"),
                            "description": course.get("description") or "EduVerse course purchase",
                        },
                        "unit_amount": int(price * 100),
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            return_url=f"{FRONTEND_URL}/student/enroll-course/{data.courseId}?checkout_success=1&session_id={{CHECKOUT_SESSION_ID}}",
            metadata={
                "courseId": data.courseId,
                "studentId": student_id,
                "tenantId": tenant_id,
                "type": "course_purchase",
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
            "stripeSessionId": session.id,
        }
    )

    return {"clientSecret": session.client_secret}


# ─── 2. Stripe Webhook (called by Stripe, not by frontend) ───────────


async def _process_successful_payment(
    course_id: str, student_id: str, tenant_id: str | None, stripe_session_id: str
):
    """
    Helper to mark a payment as completed and enroll the student.
    Used by BOTH the Webhook and the manual Confirm endpoint.
    """
    from app.crud.payments import find_pending_payment, update_payment_status, find_payment_by_session

    # 1. Idempotency check: find the payment record
    existing = await find_payment_by_session(stripe_session_id)
    
    if existing and existing.get("status") == "completed":
        logger.info("[Stripe] Payment %s already processed. Skipping.", stripe_session_id)
    else:
        # Update status to completed
        await update_payment_status(
            stripe_session_id, 
            "completed", 
            studentId=student_id, 
            tenantId=tenant_id
        )
        logger.info("[Stripe] Payment %s marked as completed.", stripe_session_id)

    # 2. Resolve profile for enrollment (handles tenant context)
    resolved = await _resolve_student_profile(student_id, tenant_id)
    if not resolved:
        logger.error("[Stripe] Could not resolve student profile for enrollment: student=%s", student_id)
        return {"success": False, "message": "Student profile not found"}

    # 3. Perform enrollment (idempotent in course_crud)
    logger.info("[Stripe] Enrolling student %s in course %s", student_id, course_id)
    enrollment = await course_crud.enroll_student(
        course_id,
        resolved["student_id"],
        resolved["tenant_id"],
        enforce_same_tenant=False,
    )
    
    if not enrollment.get("success") and enrollment.get("message") != "Already enrolled":
        logger.error("[Stripe] Enrollment failed: %s", enrollment.get("message"))
        return {"success": False, "message": enrollment.get("message")}

    return {"success": True, "message": "Enrolled successfully"}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Handles Stripe webhook events.
    On successful payment: marks payment as completed + enrolls student.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    obj = event["data"]["object"]
    metadata = getattr(obj, "metadata", {})

    # ── Handling Course Purchases & Tenant Upgrades ──
    if event_type == "checkout.session.completed" or event_type == "payment_intent.succeeded":
        session_type = metadata.get("type")
        
        if session_type == "course_purchase":
            course_id = metadata.get("courseId")
            student_id = metadata.get("studentId")
            tenant_id = metadata.get("tenantId")
            stripe_id = getattr(obj, "id", None)

            if course_id and student_id:
                await _process_successful_payment(str(course_id), str(student_id), tenant_id, str(stripe_id or ""))

        elif session_type == "tenant_upgrade" and event_type == "checkout.session.completed":
            tenant_id = metadata.get("tenantId")
            plan_id = metadata.get("planId")
            if tenant_id and plan_id:
                from app.utils.stripe_helpers import process_tenant_upgrade
                await process_tenant_upgrade(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    stripe_sub_id=getattr(obj, "subscription", None),
                    session_id=str(getattr(obj, "id", None) or ""),
                    amount_total=getattr(obj, "amount_total", 0),
                    currency=getattr(obj, "currency", "usd"),
                    metadata=dict(metadata) if metadata else {}
                )

    elif event_type == "payment_intent.payment_failed":
        await update_payment_status(obj["id"], "failed")

    return {"status": "ok"}


# ─── 3. Confirm Payment (frontend calls after successful card payment) ──


@router.get("/confirm-session/{session_id}")
async def confirm_checkout_session(
    session_id: str,
    current_user=Depends(require_role("student")),
):
    """
    Called by frontend after Stripe Embedded Checkout redirects back.
    Verifies session directly with Stripe and triggers enrollment.
    This replaces the need for a Webhook in development/local environments.
    """
    try:
        session = await asyncio.to_thread(stripe.checkout.Session.retrieve, session_id)
    except stripe.error.StripeError as e:
        logger.error("[ConfirmSession] Stripe error: %s", e)
        raise HTTPException(status_code=400, detail="Invalid checkout session")

    if getattr(session, "payment_status", None) != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed")

    raw_meta = getattr(session, "metadata", {})
    metadata = raw_meta._data if hasattr(raw_meta, "_data") else (dict(raw_meta) if isinstance(raw_meta, dict) else {})
    course_id = metadata.get("courseId")
    student_id = metadata.get("studentId")
    tenant_id = metadata.get("tenantId")

    if not course_id or not student_id:
        raise HTTPException(status_code=400, detail="Session metadata missing")

    # Verify ownership
    student_profile = await _get_current_student_profile(current_user)
    if student_id != student_profile["student_id"]:
         # Check if student_id in metadata is the userId instead
         resolved = await _resolve_student_profile(student_id, tenant_id)
         if not resolved or resolved["student_id"] != student_profile["student_id"]:
             raise HTTPException(status_code=403, detail="Payment ownership mismatch")

    # Process success (enrollment + DB update)
    result = await _process_successful_payment(course_id, student_id, tenant_id, session_id)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return {"status": "success", "courseId": course_id, "enrolled": True}


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

    raw_meta = getattr(intent, "metadata", {})
    metadata = raw_meta._data if hasattr(raw_meta, "_data") else (dict(raw_meta) if isinstance(raw_meta, dict) else {})
    course_id = metadata.get("courseId")
    student_id = metadata.get("studentId")
    tenant_id = metadata.get("tenantId")
    student_profile = await _get_current_student_profile(current_user)
    if not student_id:
        raise HTTPException(status_code=400, detail="Payment metadata missing student info")
    resolved_student = await _resolve_student_profile(str(student_id), tenant_id)
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
            studentId=resolved_student["student_id"],
            tenantId=resolved_student["tenant_id"],
        )

    if not course_id:
        raise HTTPException(status_code=400, detail="Payment metadata missing course info")
    enrollment = await course_crud.enroll_student(
        str(course_id),
        resolved_student["student_id"],
        resolved_student["tenant_id"],
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
