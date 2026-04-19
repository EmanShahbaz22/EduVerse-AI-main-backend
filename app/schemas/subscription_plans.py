from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


BillingCycle = Literal["monthly", "quarterly", "yearly"]
PlanStatus = Literal["active", "inactive"]


class SubscriptionPlanCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2, max_length=120)
    category: str = Field(default="custom", min_length=2, max_length=50)
    billingCycle: BillingCycle = "monthly"
    pricePerMonth: float = Field(default=0, ge=0)
    maxStudents: Optional[int] = Field(default=None, ge=-1)
    maxTeachers: Optional[int] = Field(default=None, ge=-1)
    maxCourses: Optional[int] = Field(default=None, ge=-1)
    aiCredits: Optional[int] = Field(default=None, ge=0)
    storageGb: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=2000)
    features: list[str] = Field(default_factory=list)
    status: PlanStatus = "active"


class SubscriptionPlanUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=50)
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    category: Optional[str] = Field(default=None, min_length=2, max_length=50)
    billingCycle: Optional[BillingCycle] = None
    pricePerMonth: Optional[float] = Field(default=None, ge=0)
    maxStudents: Optional[int] = Field(default=None, ge=-1)
    maxTeachers: Optional[int] = Field(default=None, ge=-1)
    maxCourses: Optional[int] = Field(default=None, ge=-1)
    aiCredits: Optional[int] = Field(default=None, ge=0)
    storageGb: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=2000)
    features: Optional[list[str]] = None
    status: Optional[PlanStatus] = None


class SubscriptionPlanResponse(BaseModel):
    id: str
    code: str
    name: str
    category: str
    billingCycle: BillingCycle
    pricePerMonth: float
    maxStudents: Optional[int] = None
    maxTeachers: Optional[int] = None
    maxCourses: Optional[int] = None
    aiCredits: Optional[int] = None
    storageGb: Optional[int] = None
    description: Optional[str] = None
    features: list[str] = Field(default_factory=list)
    status: PlanStatus
    createdAt: datetime
    updatedAt: Optional[datetime] = None
