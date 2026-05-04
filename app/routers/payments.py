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
            ui_mode="embedded",
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
    Uses metadata to ensure we find the correct record even if IDs mismatch.
    """
    from app.crud.payments import find_pending_payment, update_payment_status

    # 1. Idempotency check: find the pending record
    # We look it up by student/course to be safe, then update by its session ID
    pending = await find_pending_payment(student_id, course_id)
    if not pending:
        # If no pending record found, it might already be completed or created externally.
        # We'll still try to enroll to be safe.
        logger.warning(
            "[Webhook] No pending payment found for student=%s course=%s",
            student_id, course_id
        )
    
    # 2. Update status (if we have a session ID)
    target_id = stripe_session_id or (pending["stripeSessionId"] if pending else None)
    if target_id:
        await update_payment_status(target_id, "completed", studentId=student_id, tenantId=tenant_id)

    # 3. Resolve profile for enrollment (handles tenant context)
    resolved = await _resolve_student_profile(student_id, tenant_id)
    if not resolved:
        logger.error("[Webhook] Could not resolve student profile for enrollment: student=%s", student_id)
        return

    # 4. Perform enrollment
    logger.info("[Webhook] Enrolling student %s in course %s", student_id, course_id)
    enrollment = await course_crud.enroll_student(
        course_id,
        resolved["student_id"],
        resolved["tenant_id"],
        enforce_same_tenant=False,
    )
    
    if not enrollment.get("success") and enrollment.get("message") != "Already enrolled":
        logger.error("[Webhook] Enrollment failed: %s", enrollment.get("message"))
        if target_id:
            await update_payment_status(target_id, "completed", enrollmentError=enrollment.get("message"))


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
    metadata = obj.get("metadata", {})

    # ── Handling Course Purchases & Tenant Upgrades ──
    if event_type == "checkout.session.completed" or event_type == "payment_intent.succeeded":
        session_type = metadata.get("type")
        
        if session_type == "course_purchase":
            course_id = metadata.get("courseId")
            student_id = metadata.get("studentId")
            tenant_id = metadata.get("tenantId")
            stripe_id = obj.get("id")

            if course_id and student_id:
                await _process_successful_payment(course_id, student_id, tenant_id, stripe_id)

        elif session_type == "tenant_upgrade" and event_type == "checkout.session.completed":
            tenant_id = metadata.get("tenantId")
            plan_id = metadata.get("planId")
            if tenant_id and plan_id:
                from datetime import timedelta
                now = datetime.utcnow()
                plan_doc = await db.subscriptionPlans.find_one({"_id": ObjectId(plan_id)})
                if plan_doc:
                    billing_cycle = plan_doc.get("billingCycle", "monthly")
                    expiry = now + timedelta(days=365) if billing_cycle == "yearly" else now + timedelta(days=30)
                    
                    await db.tenants.update_one(
                        {"_id": ObjectId(tenant_id)},
                        {"$set": {
                            "subscriptionId": ObjectId(plan_id),
                            "subscriptionPlan": plan_doc.get("name"),
                            "subscriptionCategory": plan_doc.get("category", "paid"),
                            "stripeSubscriptionId": obj.get("subscription"),
                            "subscriptionStartDate": now,
                            "subscriptionExpiryDate": expiry,
                            "updatedAt": now
                        }}
                    )
                    
                    # Record the payment for the admin history
                    from app.crud.payments import create_payment
                    await create_payment({
                        "tenantId": tenant_id,
                        "paymentType": "subscription",
                        "amount": obj.get("amount_total", 0) / 100,
                        "currency": obj.get("currency", "usd"),
                        "status": "completed",
                        "stripeSessionId": obj.get("id"),
                        "metadata": metadata
                    })

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
    Called by frontend after Stripe Embedded Checkout redirects back with
    ?checkout_success=1&session_id=cs_...
    Verifies the checkout session with Stripe, marks payment as completed,
    and enrolls the student in the course. Safe and idempotent.
    """
    # 1. Retrieve the checkout session from Stripe
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.retrieve, session_id
        )
    except stripe.error.StripeError as e:
        logger.error("[ConfirmSession] Stripe error retrieving session %s: %s", session_id, e)
        raise HTTPException(status_code=400, detail="Invalid checkout session")

    # 2. Verify payment is paid
    if session.get("payment_status") != "paid":
        raise HTTPException(
            status_code=400,
            detail=f"Payment not completed yet (status: {session.get('payment_status')})"
        )

    # 3. Extract metadata stored when session was created
    metadata = session.get("metadata", {})
    course_id = metadata.get("courseId")
    student_id = metadata.get("studentId")
    tenant_id = metadata.get("tenantId")

    if not course_id or not student_id:
        raise HTTPException(status_code=400, detail="Session metadata is incomplete")

    # 4. Verify the logged-in student owns this payment
    student_profile = await _get_current_student_profile(current_user)
    resolved_student = await _resolve_student_profile(student_id, tenant_id)
    if not resolved_student:
        raise HTTPException(status_code=403, detail="Could not resolve student profile")

    if resolved_student["student_id"] != student_profile["student_id"]:
        raise HTTPException(status_code=403, detail="This payment does not belong to you")

    # 5. Mark payment as completed (idempotent — skip if already done)
    existing = await find_payment_by_session(session_id)
    if existing and existing.get("status") != "completed":
        await update_payment_status(
            session_id,
            "completed",
            studentId=resolved_student["student_id"],
            tenantId=resolved_student["tenant_id"],
        )
    elif not existing:
        # Edge case: no payment record found (e.g., webhook already cleaned up)
        logger.warning("[ConfirmSession] No payment record found for session %s, proceeding with enrollment only", session_id)

    # 6. Enroll the student (idempotent)
    logger.info("[ConfirmSession] Enrolling student %s in course %s", resolved_student["student_id"], course_id)
    enrollment = await course_crud.enroll_student(
        course_id,
        resolved_student["student_id"],
        resolved_student["tenant_id"],
        enforce_same_tenant=False,
    )
    if not enrollment.get("success") and enrollment.get("message") != "Already enrolled":
        logger.error("[ConfirmSession] Enrollment failed: %s", enrollment.get("message"))
        raise HTTPException(status_code=400, detail=enrollment.get("message", "Enrollment failed"))

    logger.info("[ConfirmSession] Success — student %s enrolled in course %s", resolved_student["student_id"], course_id)
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
            studentId=resolved_student["student_id"],
            tenantId=resolved_student["tenant_id"],
        )

    enrollment = await course_crud.enroll_student(
        course_id,
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
