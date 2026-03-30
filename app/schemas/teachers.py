from pydantic import BaseModel, EmailStr, Field
from typing import Dict, List, Optional
from datetime import datetime


class TeacherCreate(BaseModel):
    fullName: str
    email: EmailStr
    password: str
    profileImageURL: Optional[str] = ""
    assignedCourses: List[str] = []
    contactNo: Optional[str]
    country: Optional[str]
    status: str = "active"
    role: str = "teacher"
    qualifications: List[str] = []
    subjects: List[str] = []
    tenantId: str


class TeacherUpdate(BaseModel):
    fullName: Optional[str] = None
    profileImageURL: Optional[str] = None
    assignedCourses: Optional[List[str]] = None
    contactNo: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None
    qualifications: Optional[List[str]] = None
    subjects: Optional[List[str]] = None


class TeacherResponse(BaseModel):
    id: str
    fullName: str
    email: str
    profileImageURL: str
    assignedCourses: List[str]
    contactNo: Optional[str]
    country: Optional[str]
    status: str
    role: str
    createdAt: datetime
    updatedAt: datetime
    lastLogin: Optional[datetime]
    qualifications: List[str]
    subjects: List[str]
    tenantId: str

    model_config = {"from_attributes": True}


class ChangePassword(BaseModel):
    oldPassword: str
    newPassword: str


class TeacherBulkInviteRequest(BaseModel):
    emails: List[EmailStr]
    defaultPassword: Optional[str] = None
    status: str = "active"
    tenantId: Optional[str] = None


class TeacherBulkInviteResponse(BaseModel):
    created: int
    linkedExisting: int
    skipped: int
    errors: List[str]
    generatedPasswords: Dict[str, str] = Field(default_factory=dict)
