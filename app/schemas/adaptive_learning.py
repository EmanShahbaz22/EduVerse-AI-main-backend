from __future__ import annotations
"""
Pydantic schemas for the Adaptive Learning AI features.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


# ──────────────────────────────────────────────
# Request Models
# ──────────────────────────────────────────────

class LessonGenerationRequest(BaseModel):
    """What the frontend sends when requesting an AI lesson."""
    courseId: str = Field(..., description="The course ID this lesson is for")
    quizId: str = Field(..., description="The quiz that triggered this lesson")
    topic: str = Field(..., description="The topic/subject for the lesson")
    weakAreas: Optional[str] = Field(
        None,
        description="Comma-separated weak areas the student struggled with"
    )


class ClassifyStudentRequest(BaseModel):
    """Request to classify a student based on quiz performance."""
    courseId: str = Field(..., description="The course ID")
    quizId: str = Field(..., description="The quiz ID")
    scorePercentage: float = Field(..., ge=0, le=100, description="Quiz score percentage (0-100)")
    timeSpentSeconds: Optional[float] = Field(None, ge=0, description="Time spent on quiz in seconds")
    timeLimitSeconds: Optional[float] = Field(None, ge=0, description="Quiz time limit in seconds")


# ──────────────────────────────────────────────
# Response Models
# ──────────────────────────────────────────────

class ClassificationResponse(BaseModel):
    """The classification result returned to the frontend."""
    id: str
    pace: str = Field(..., description="slow, average, or fast")
    score: float
    factors: List[str]
    courseId: Optional[str] = None
    quizId: Optional[str] = None
    classifiedAt: Optional[str] = None


class GeneratedLessonResponse(BaseModel):
    """A single AI-generated lesson."""
    id: str
    title: str
    content: str
    difficulty: str
    pace: Optional[str] = None
    estimatedDurationMinutes: int = 10
    keyConcepts: List[str] = []
    summary: str = ""
    courseId: Optional[str] = None
    quizId: Optional[str] = None
    generatedAt: Optional[str] = None


class LessonGenerationResponse(BaseModel):
    """The full response after generating a lesson."""
    classification: ClassificationResponse
    lesson: GeneratedLessonResponse
    studentId: str
    courseId: str
    quizId: str
