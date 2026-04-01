import logging
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Depends
from app.schemas.adaptive_learning import AIChatRequest, AIChatResponse
from app.services.chat_tutor import ChatTutorService
from app.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai-tutor",
    tags=["AI Tutor"]
)

@router.post("/chat", response_model=AIChatResponse)
async def chat_with_tutor(
    request: AIChatRequest,
    current_user: Any = Depends(get_current_user)
):
    """
    SendMessage to the AI tutor with conversation history.
    """
    try:
        student_id = current_user.get("student_id") or current_user.get("user_id")
        
        response_data = await ChatTutorService.get_chat_response(
            student_id=student_id,
            course_id=request.courseId,
            message=request.message,
            lesson_id=request.lessonId
        )
        
        return AIChatResponse(**response_data)
        
    except Exception as e:
        err_msg = str(e).lower()
        if any(kw in err_msg for kw in ["429", "resource_exhausted", "quota"]):
            raise HTTPException(
                status_code=429,
                detail="Gemini API quota exceeded. Please try again in 60 seconds."
            )
        logger.error(f"Error in ai_tutor router: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Tutor service error: {str(e)}"
        )

@router.delete("/session/{course_id}")
async def clear_chat_session(
    course_id: str,
    current_user: Any = Depends(get_current_user)
):
    """
    Clears the chat history for a specific course session.
    """
    try:
        student_id = current_user.get("student_id") or current_user.get("user_id")
        success = await ChatTutorService.clear_session(student_id, course_id)
        
        if success:
            return {"message": "Chat session cleared successfully."}
        else:
            raise HTTPException(status_code=500, detail="Failed to clear session.")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
