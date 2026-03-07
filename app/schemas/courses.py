from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from bson import ObjectId

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, _schema_generator):
        return {"type": "string"}


# Schema for a single lesson within a module
class LessonSchema(BaseModel):
    id: str
    title: str
    type: str = "video"  # video, reading, quiz
    duration: Optional[str] = None
    content: Optional[str] = None
    order: int = 0

# Schema for a single course module (title, description, content, etc.)
class ModuleSchema(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = None
    content: Optional[str] = None
    lessons: List[LessonSchema] = []
    order: int = 0

# Base schema containing shared fields for all course-related operations
class CourseBase(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    category: str
    level: str = "Beginner"  # Possible values: Beginner, Intermediate, Advanced
    status: str = "draft"  # Possible values: draft, published
    courseCode: Optional[str] = None
    duration: Optional[str] = None
    thumbnailUrl: Optional[str] = ""
    modules: List[ModuleSchema] = []
    isPublic: bool = True  # true = in marketplace, false = private 
    isFree: bool = True
    price: Optional[float] = 0
    currency: Optional[str] = "USD"
    # Extended Metadata
    instructorBio: Optional[str] = None
    hasCertificate: bool = False
    hasBadges: bool = False
    hasLifetimeAccess: bool = False

# Schema for creating a new course (requires IDs for teacher and tenant)
class CourseCreate(CourseBase):
    teacherId: str
    tenantId: str
    enrolledStudents: int = 0

# Schema for updating an existing course (all fields are optional)
class CourseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = None
    level: Optional[str] = None
    status: Optional[str] = None
    courseCode: Optional[str] = None  
    duration: Optional[str] = None
    thumbnailUrl: Optional[str] = None
    modules: Optional[List[ModuleSchema]] = None
    teacherId: Optional[str] = None
    tenantId: Optional[str] = None
    isPublic: Optional[bool] = None
    isFree: Optional[bool] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    instructorBio: Optional[str] = None
    hasCertificate: Optional[bool] = None
    hasBadges: Optional[bool] = None
    hasLifetimeAccess: Optional[bool] = None

# Schema for the full course data as returned in API responses
class CourseResponse(CourseBase):
    id: str = Field(alias="_id")
    teacherId: str
    tenantId: str
    instructorName: Optional[str] = None
    enrolledStudents: int = 0
    createdAt: datetime
    updatedAt: datetime

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Schema for enrolling a student into a specific course
class CourseEnrollment(BaseModel):
    courseId: str
    studentId: Optional[str] = None
    tenantId: Optional[str] = None

# Schema for course data including student progress tracking
class CourseWithProgress(CourseResponse):
    progress: Optional[int] = 0  # Percentage (0-100)
    lessonsCompleted: Optional[int] = 0
    totalLessons: Optional[int] = 0
    nextLesson: Optional[str] = None

# Schema for reordering lessons within a module
class ReorderLessonsRequest(BaseModel):
    moduleId: str
    lessonIds: List[str]  # Ordered list of lesson IDs

# Schema for reordering modules within a course
class ReorderModulesRequest(BaseModel):
    moduleIds: List[str]  # Ordered list of module IDs

# Schema for publishing/unpublishing a course
class PublishCourseRequest(BaseModel):
    publish: bool = True  # True to publish, False to unpublish
