from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class AddPointsRequest(BaseModel):
    points: int
    reason: Optional[str] = "Course Activity"


class WeeklyTimeRequest(BaseModel):
    weekStart: datetime
    minutes: int


class BadgeRequest(BaseModel):
    title: str
    earnedOn: datetime


class CertificateRequest(BaseModel):
    title: str
    issuedOn: datetime
    fileURL: str


class StudentPerformanceResponse(BaseModel):
    id: str
    tenantId: str

    totalPoints: int
    pointsThisWeek: int
    level: int
    xp: int
    xpToNextLevel: int

    courseStats: Optional[List[dict]] = []
    badges: Optional[List[dict]] = []
    pointsHistory: Optional[List[dict]] = []
    certificates: Optional[List[dict]] = []
    weeklyStudyTime: Optional[List[dict]] = []
    LeaderBoard: Optional[List[dict]] = []

    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True

class CourseProgressRequest(BaseModel):
    courseId: str
    completionPercentage: int
    lastActive: datetime
