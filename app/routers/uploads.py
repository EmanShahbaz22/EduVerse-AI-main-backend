import io
import os
import re
import uuid
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/uploads", tags=["Uploads"])

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
FILE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}\.(pdf|docx)$")


def _validate_extension(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF and DOCX files are allowed")
    return ext


def _sniff_file_type(ext: str, content: bytes) -> str:
    if ext == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "File content does not match PDF format")
        return ALLOWED_EXTENSIONS[".pdf"]

    if ext == ".docx":
        if not content.startswith(b"PK"):
            raise HTTPException(400, "File content does not match DOCX format")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or not any(
                    name.startswith("word/") for name in names
                ):
                    raise HTTPException(400, "Invalid DOCX file structure")
        except zipfile.BadZipFile:
            raise HTTPException(400, "Invalid DOCX file")
        return ALLOWED_EXTENSIONS[".docx"]

    raise HTTPException(400, "Unsupported file type")


def _resolve_file_path(file_id: str) -> str:
    if not FILE_ID_PATTERN.match(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return path


@router.post("/assignment")
async def upload_assignment(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    ext = _validate_extension(file.filename or "")
    content = await file.read()

    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(400, "File exceeds 10 MB limit")

    tenant_id = current_user.get("tenant_id")
    if tenant_id:
        from app.utils.limits import check_tenant_limits
        await check_tenant_limits(tenant_id, "storage", len(content))

    detected_mime = _sniff_file_type(ext, content)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_id = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, file_id)

    with open(file_path, "wb") as stream:
        stream.write(content)

    if tenant_id:
        from bson import ObjectId
        from app.db.database import db
        await db.tenants.update_one(
            {"_id": ObjectId(tenant_id)},
            {"$inc": {"totalStorageUsedBytes": len(content)}}
        )

    return {
        "url": f"/uploads/assignment/{file_id}",
        "filename": file.filename,
        "contentType": detected_mime,
    }


@router.get("/assignment/{file_id}")
async def get_assignment_file(file_id: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    path = _resolve_file_path(file_id)
    ext = os.path.splitext(file_id)[1].lower()
    media_type = ALLOWED_EXTENSIONS.get(ext, "application/octet-stream")
    return FileResponse(path=path, media_type=media_type, filename=file_id)

@router.get("/certificate/{file_id}")
async def get_certificate_file(file_id: str):
    # Certificates are public to anyone with the link
    if not FILE_ID_PATTERN.match(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    
    path = os.path.join(UPLOAD_DIR, "certificates", file_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Certificate not found")
        
    ext = os.path.splitext(file_id)[1].lower()
    media_type = ALLOWED_EXTENSIONS.get(ext, "application/pdf")
    return FileResponse(path=path, media_type=media_type, filename=file_id)
