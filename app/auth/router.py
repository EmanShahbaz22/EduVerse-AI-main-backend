from fastapi.security import OAuth2PasswordBearer

# auto_error=False: allows cookie-based auth as primary, header as fallback
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)
