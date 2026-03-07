from fastapi import APIRouter, HTTPException

from app.crud.auth_signup import signup_student as signup_student_service
from app.schemas.users import UserCreate

router = APIRouter(prefix="/auth/student", tags=["Student Authentication"])


@router.post("/signup")
async def signup_student(payload: UserCreate):
    if payload.role != "student":
        raise HTTPException(403, "This endpoint is only for student signup")

    user = await signup_student_service(payload.model_dump())

    return {"message": "Student created successfully", "user": user}
