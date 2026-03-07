from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from app.auth.auth_service import login_user
from app.utils.security import set_auth_cookie, clear_auth_cookie
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    result = await login_user(form_data.username, form_data.password)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = result["access_token"]
    response = JSONResponse(
        content=jsonable_encoder(
            {
                "access_token": token,
                "token_type": "bearer",
                "user": result["user"],
            }
        )
    )
    set_auth_cookie(response, token)
    return response


@router.post("/logout")
async def logout():
    response = JSONResponse(content={"message": "Logged out"})
    clear_auth_cookie(response)
    return response


@router.get("/me")
async def get_me(current_user=Depends(get_current_user)):
    return current_user
