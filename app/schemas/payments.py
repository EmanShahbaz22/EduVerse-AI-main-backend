# app/schemas/payments.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CheckoutRequest(BaseModel):
    """What the frontend sends to create a checkout session."""

    courseId: str
    tenantId: Optional[str] = None


class CheckoutResponse(BaseModel):
    """What the backend returns — a Stripe checkout URL."""

    checkoutUrl: str


class PaymentResponse(BaseModel):
    """Payment record returned to the frontend."""

    id: str
    courseId: str
    studentId: str
    tenantId: str
    amount: float
    currency: str
    status: str  # pending | completed | failed
    stripeSessionId: Optional[str] = None
    createdAt: datetime
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True
