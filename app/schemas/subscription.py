from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class PaymentHistory(BaseModel):
    paymentId: str
    amount: float
    date: datetime
    method: str
    status: str

class Subscription(BaseModel):
    id: Optional[str] = None
    plan: str
    max_students: int
    max_teachers: int
    max_courses: int
    ai_credits: int
    storage_gb: int
    price_per_month: float
    billing_cycle: str
    status: str
    expiry_date: datetime
    payment_history: Optional[List[PaymentHistory]] = Field(default_factory=list)
    userId: Optional[str] = None
    tenantId: str

    class Config:
        from_attributes = True
        populate_by_name = True
