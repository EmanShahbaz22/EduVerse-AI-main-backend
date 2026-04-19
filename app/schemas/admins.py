from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

# ------------------ Request Models ------------------


class AdminCreate(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str = Field(..., min_length=6)
    confirmPassword: str
    country: str
    phone: str


class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class AdminUpdateProfile(BaseModel):
    fullName: Optional[str] = None
    country: Optional[str] = None
    contactNo: Optional[str] = None
    profileImageURL: Optional[str] = None


class AdminUpdatePassword(BaseModel):
    oldPassword: str
    newPassword: str = Field(..., min_length=6)


# ------------------ Response Models ------------------



class AdminResponse(BaseModel):
    id: str
    fullName: str
    email: EmailStr
    country: Optional[str] = None
    contactNo: Optional[str] = None
    profileImageURL: Optional[str] = ""
    status: Optional[str] = "active"
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    updatedAt: Optional[datetime] = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True
