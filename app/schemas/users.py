from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Literal, Optional
from pydantic import ConfigDict


class UserBase(BaseModel):
    fullName: str
    email: EmailStr
    role: Literal["student", "teacher", "admin", "super_admin", "super-admin"]
    status: str = "active"
    profileImageURL: Optional[str] = None
    contactNo: Optional[str] = None
    country: Optional[str] = None
    tenantId: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)
    model_config = ConfigDict(validate_assignment=True)


class AdminSignupRequest(UserBase):
    password: str = Field(..., min_length=6)
    tenantName: str
    tenantLogoUrl: Optional[str] = None

    model_config = ConfigDict(validate_assignment=True)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=72)

    model_config = ConfigDict(validate_assignment=True)


class UserResponse(UserBase):
    id: str
    tenantId: Optional[str] = Field(None, alias="tenant_id")
    createdAt: datetime
    updatedAt: datetime
    lastLogin: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
