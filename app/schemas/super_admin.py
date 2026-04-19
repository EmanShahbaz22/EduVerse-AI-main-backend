from typing import Optional
from pydantic import BaseModel
from datetime import datetime
from app.schemas.users import UserResponse


class SuperAdminCreate(BaseModel):
    userId: str


class SuperAdminUpdate(BaseModel):
    # ---- user fields ----
    fullName: Optional[str] = None
    profileImageURL: Optional[str] = None
    contactNo: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None

    model_config = {"from_attributes": True}


class SuperAdminResponse(BaseModel):
    id: str
    userId: str
    user: UserResponse  # NESTED USER
    createdAt: datetime
    updatedAt: datetime

    model_config = {"from_attributes": True}

class ActivityDataPoint(BaseModel):
    category: str
    value: int
    color: str

class OrganizationRow(BaseModel):
    name: str
    teachers: int
    students: int
    courses: int

class TenantGrowthPoint(BaseModel):
    month: str
    tenants: int

class SuperAdminDashboardResponse(BaseModel):
    totalTenants: int
    activeUsers: str
    totalCourses: int
    revenue: str
    tenantGrowthData: list[TenantGrowthPoint]
    activityData: list[ActivityDataPoint]
    organizationRows: list[OrganizationRow]
