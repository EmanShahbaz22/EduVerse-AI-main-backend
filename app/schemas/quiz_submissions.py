from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel

class AnswerItem(BaseModel):
    """
    A single answer provided by the student.
    - questionIndex: index of the question in quiz.questions list (0-based)
    - selected: the selected option text (should match one of the options)
    """
    questionIndex: int
    selected: str

class QuizSubmissionCreate(BaseModel):
    """
    Payload when a student submits answers.
    - answers: list of AnswerItem (for auto marking)
    - percentage and obtainedMarks are optional and will be set by grading process
    """
    quizId: str                # quiz being submitted
    courseId: str              # course to which the quiz belongs
    # Accept flexible payloads (AnswerItem dicts or raw strings). Normalised in CRUD.
    answers: list[Any]

class QuizSubmissionResponse(BaseModel):
    """
    What the API returns for a submission.
    - includes answers so frontend can display what was submitted
    - status indicates graded/pending
    """

    id: str
    studentId: str
    quizId: str
    courseId: str
    tenantId: str
    submittedAt: datetime
    answers: list[Dict]  # list of answer objects (questionIndex + selected)
    percentage: Optional[float] = None
    obtainedMarks: Optional[float] = None
    status: str
