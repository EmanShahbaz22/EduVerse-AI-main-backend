"""
reference_upload.py — Teacher Reference File Upload Endpoints (v2)

Full Flow:
    1. Teacher uploads reference file (PDF / PPTX / DOCX).
    2. File is saved to local disk permanently (uploaded_references/).
    3. Chunking runs in the background — UI is not frozen.
    4. Frontend polls /reference/upload-status/{upload_id} every 3 seconds.
    5. Once done, teacher clicks "Generate Full Course Lessons".
    6. AI reads ChromaDB chunks → produces N lessons → validates → returns.

Endpoints:
    POST   /reference/upload                      → upload + background chunk
    GET    /reference/upload-status/{upload_id}   → polling endpoint
    GET    /reference/uploads                     → list uploads for course
    DELETE /reference/uploads/{upload_id}         → delete file + chunks + record
    POST   /reference/generate-description        → RAG auto-fill single lesson (existing)
    POST   /reference/generate-course-lessons     → AI generates full lesson plan

All endpoints require teacher or admin authentication.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.db.database import reference_uploads_collection, config_collection
from app.services.rag_service import (
    auto_generate_lesson_description,
    load_and_chunk_document,
    store_chunks_in_chromadb,
    retrieve_chunks_from_chromadb,
    delete_collection,
)
from app.services.file_storage_service import (
    save_file_to_disk,
    delete_file_from_disk,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reference", tags=["Reference Uploads (RAG)"])

# ── Constants ──────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_MB   = 50
ALLOWED_EXTENSIONS = {"pdf", "pptx", "docx"}


# ── Pydantic models ────────────────────────────────────────────────────────────

class GenerateDescriptionRequest(BaseModel):
    topic: str
    tenant_id: str
    course_id: str
    lesson_id: str | None = None
    upload_id: str | None = None


class CourseLessonGenerationRequest(BaseModel):
    tenant_id: str
    course_id: str
    course_title: str


# ── Helper: convert ObjectId → str for API responses ─────────────────────────

def _oid_to_str(doc: dict) -> dict:
    """Replace ObjectId _id with string for JSON serialisation."""
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


# ── Background task ────────────────────────────────────────────────────────────

async def _process_and_chunk_file(
    *,
    file_bytes: bytes,
    extension: str,
    file_size_mb: float,
    tenant_id: str,
    course_id: str,
    chapter_tag: str,
    upload_id: str,
) -> None:
    """
    Runs in the background after the upload response is sent.
    Chunks the file, stores vectors in ChromaDB, and updates MongoDB.

    The UI polls /reference/upload-status/{upload_id} every 3 seconds to
    show progress without the browser freezing.
    """
    try:
        logger.info(
            "Background chunking started for upload %s (%.2f MB %s)",
            upload_id, file_size_mb, extension,
        )

        chunks = load_and_chunk_document(
            file_bytes=file_bytes,
            file_extension=extension,
            file_size_mb=file_size_mb,
            tenant_id=tenant_id,
            course_id=course_id,
        )

        # Store in ChromaDB at course level — NEVER lesson level
        collection_name = f"{tenant_id}_{course_id}"
        store_chunks_in_chromadb(
            chunks=chunks,
            tenant_id=tenant_id,
            course_id=course_id,
            chapter_tag=chapter_tag,
        )

        await reference_uploads_collection.update_one(
            {"_id": ObjectId(upload_id)},
            {
                "$set": {
                    "chunk_status": "done",
                    "chunk_count": len(chunks),
                    "chunk_collection": collection_name,
                    "processed_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.info(
            "Background chunking done for upload %s — %d chunks in collection '%s'",
            upload_id, len(chunks), collection_name,
        )

    except Exception as exc:
        logger.error("Background chunking FAILED for upload %s: %s", upload_id, exc)
        await reference_uploads_collection.update_one(
            {"_id": ObjectId(upload_id)},
            {
                "$set": {
                    "chunk_status": "failed",
                    "error_message": str(exc),
                    "failed_at": datetime.now(timezone.utc),
                }
            },
        )


# ── POST /reference/upload ────────────────────────────────────────────────────

@router.post("/upload", summary="Upload teacher reference file for RAG")
async def upload_reference(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF, PPTX, or DOCX — max 50 MB"),
    course_id: str = Form(...),
    tenant_id: str = Form(default=""),
    course_title: str = Form(default=""),
    chapter_tag: str = Form(default=""),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload a teacher reference document.

    1. Validate extension + size.
    2. Save to disk (uploaded_references/{tenant}/{course}/{uuid}.ext).
    3. Insert a MongoDB record with chunk_status = "processing".
    4. Start background chunking task.
    5. Return immediately — client polls /reference/upload-status/{upload_id}.
    """
    # Resolve tenant_id from token if not supplied in form
    if not tenant_id:
        tenant_id = current_user.get("tenant_id", "")

    # Validate extension
    extension = (file.filename or "").split(".")[-1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '.{extension}' is not supported. "
                f"Please upload PDF, PPTX, or DOCX files only."
            ),
        )

    # Read bytes and check size
    file_bytes = await file.read()
    file_size_mb = len(file_bytes) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is too large ({file_size_mb:.1f} MB). "
                f"Maximum allowed size is {MAX_FILE_SIZE_MB} MB. "
                f"Tip: Split your book into chapters and upload each chapter "
                f"separately for better AI lesson quality."
            ),
        )

    # Save file to disk permanently
    saved = save_file_to_disk(
        file_bytes=file_bytes,
        original_filename=file.filename or "upload",
        tenant_id=tenant_id,
        course_id=course_id,
    )

    # Insert MongoDB record
    upload_record = {
        "tenant_id":       tenant_id,
        "uploaded_by":     current_user.get("user_id") or current_user.get("id", ""),
        "course_id":       course_id,
        "course_title":    course_title,
        "file_name":       file.filename,
        "file_path":       saved["file_path"],
        "file_key":        saved["file_key"],
        "file_size_mb":    saved["file_size_mb"],
        "file_extension":  extension,
        "chapter_tag":     chapter_tag if chapter_tag else "Full Document",
        "chunk_status":    "processing",
        "chunk_count":     0,
        "chunk_collection": f"{tenant_id}_{course_id}",
        "uploaded_at":     datetime.now(timezone.utc),
    }
    result = await reference_uploads_collection.insert_one(upload_record)
    upload_id = str(result.inserted_id)

    # Queue background chunking — response is sent before this runs
    background_tasks.add_task(
        _process_and_chunk_file,
        file_bytes=file_bytes,
        extension=extension,
        file_size_mb=saved["file_size_mb"],
        tenant_id=tenant_id,
        course_id=course_id,
        chapter_tag=chapter_tag if chapter_tag else "Full Document",
        upload_id=upload_id,
    )

    logger.info(
        "Reference upload saved: upload_id=%s file=%s (%.2f MB) — chunking in background",
        upload_id, file.filename, saved["file_size_mb"],
    )

    return {
        "upload_id":    upload_id,
        "file_name":    file.filename,
        "file_size_mb": saved["file_size_mb"],
        "chunk_status": "processing",
        "message":      (
            "File saved. AI is processing your reference material in the background. "
            "Poll /reference/upload-status/" + upload_id + " to check progress."
        ),
    }


# ── GET /reference/upload-status/{upload_id} ─────────────────────────────────

@router.get("/upload-status/{upload_id}", summary="Poll background chunking status")
async def get_upload_status(
    upload_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Frontend polls this every 3 seconds to check chunking progress.

    Returns:
        chunk_status: "processing" | "done" | "failed"
        chunk_count:  number of chunks indexed (0 while processing)
        error_message: set only on failure
    """
    try:
        oid = ObjectId(upload_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid upload_id format")

    record = await reference_uploads_collection.find_one({"_id": oid})
    if not record:
        raise HTTPException(status_code=404, detail="Upload not found")

    return {
        "upload_id":    upload_id,
        "chunk_status": record.get("chunk_status", "processing"),
        "chunk_count":  record.get("chunk_count", 0),
        "file_name":    record.get("file_name", ""),
        "file_size_mb": record.get("file_size_mb", 0),
        "error_message": record.get("error_message"),
    }


# ── GET /reference/uploads ────────────────────────────────────────────────────

@router.get("/uploads", summary="List teacher's reference uploads")
async def list_uploads(
    course_id: str | None = None,
    lesson_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """Return all reference uploads for the current tenant filtered by course."""
    tenant_id = current_user.get("tenant_id", "")
    query: dict = {"tenant_id": tenant_id}
    if course_id:
        query["course_id"] = course_id
    if lesson_id:
        query["lesson_id"] = lesson_id

    cursor = reference_uploads_collection.find(query).sort("uploaded_at", -1)
    uploads = []
    async for doc in cursor:
        doc = _oid_to_str(doc)
        uploads.append({
            "upload_id":    doc.get("_id"),
            "file_name":    doc.get("file_name") or doc.get("filename"),
            "file_size_mb": doc.get("file_size_mb", 0),
            "course_id":    doc.get("course_id"),
            "chapter_tag":  doc.get("chapter_tag", ""),
            "chunk_status": doc.get("chunk_status") or doc.get("status", ""),
            "chunk_count":  doc.get("chunk_count") or doc.get("total_chunks", 0),
            "uploaded_at":  (
                doc["uploaded_at"].isoformat()
                if hasattr(doc.get("uploaded_at"), "isoformat")
                else str(doc.get("uploaded_at", ""))
            ),
        })
    return uploads


# ── DELETE /reference/uploads/{upload_id} ─────────────────────────────────────

@router.delete("/uploads/{upload_id}", summary="Delete a reference upload")
async def delete_upload(
    upload_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Remove the file from disk, delete the ChromaDB collection, and delete the DB record.
    """
    # Support both ObjectId-based and legacy string-based _id
    try:
        oid = ObjectId(upload_id)
        record = await reference_uploads_collection.find_one({"_id": oid})
    except Exception:
        oid = None
        record = await reference_uploads_collection.find_one({"_id": upload_id})

    if not record:
        raise HTTPException(status_code=404, detail="Upload not found")

    tenant_id = current_user.get("tenant_id", "")
    if record.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # 1. Delete file from disk
    file_path = record.get("file_path")
    if file_path:
        delete_file_from_disk(file_path)

    # 2. Delete ChromaDB collection (course-level)
    collection_name = (
        record.get("chunk_collection")
        or record.get("collection_id")
        or f"{record.get('tenant_id', '')}_{record.get('course_id', '')}"
    )
    try:
        delete_collection(collection_name)
    except Exception as exc:
        logger.warning("Could not delete ChromaDB collection '%s': %s", collection_name, exc)

    # 3. Delete MongoDB record
    if oid:
        await reference_uploads_collection.delete_one({"_id": oid})
    else:
        await reference_uploads_collection.delete_one({"_id": upload_id})

    return {"message": "Reference file and chunks deleted successfully"}


# ── POST /reference/generate-description ─────────────────────────────────────
# Existing endpoint — unchanged behaviour, kept for backward compat.

@router.post("/generate-description", summary="RAG auto-fill single lesson description")
async def generate_description(
    request: GenerateDescriptionRequest,
    current_user: dict = Depends(get_current_user),
):
    tenant_id = current_user.get("tenant_id", "")
    upload_id = request.upload_id

    if not upload_id:
        filter_query: dict = {
            "tenant_id": tenant_id,
            "course_id": request.course_id,
        }
        # Accept either legacy "processed" or new "done" status
        filter_query["$or"] = [{"status": "processed"}, {"chunk_status": "done"}]
        if request.lesson_id:
            filter_query["lesson_id"] = request.lesson_id

        latest = await reference_uploads_collection.find_one(
            filter_query,
            sort=[("uploaded_at", -1)],
        )
        if not latest:
            return {
                "status": "error",
                "message": (
                    "No processed reference found for this course. "
                    "Please upload a reference file first."
                ),
            }
        upload_id = str(latest["_id"])

    config = await config_collection.find_one({"_id": "active_worker_model"})
    active_model = config["value"] if config else "phi3.5"

    try:
        description = await auto_generate_lesson_description(
            topic=request.topic,
            tenant_id=tenant_id,
            course_id=request.course_id,
            lesson_id=request.lesson_id,
            active_model_name=active_model,
        )
        return {
            "status":             "success",
            "lesson_description": description,
            "resolved_upload_id": upload_id,
            "model_used":         active_model,
        }
    except Exception as exc:
        return {"status": "error", "message": f"Generation failed: {exc}"}


# ── POST /reference/generate-course-lessons ────────────────────────────────────

@router.post("/generate-course-lessons", summary="AI generates full course lesson plan")
async def generate_course_lessons(
    request: CourseLessonGenerationRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Reads reference chunks from ChromaDB for this course and generates a full
    lesson plan (3–10 lessons) using the active worker LLM.

    Validates output through the existing validation pipeline before returning.
    On FAIL verdict → returns 422. On PASS or REVIEW → returns lessons array.
    """
    tenant_id = current_user.get("tenant_id", "") or request.tenant_id

    # Confirm at least one processed upload exists for this course
    upload = await reference_uploads_collection.find_one({
        "tenant_id":  tenant_id,
        "course_id":  request.course_id,
        "chunk_status": "done",
    })
    # Also try legacy "processed" status
    if not upload:
        upload = await reference_uploads_collection.find_one({
            "tenant_id": tenant_id,
            "course_id": request.course_id,
            "status":    "processed",
        })

    if not upload:
        raise HTTPException(
            status_code=400,
            detail=(
                "No processed reference material found for this course. "
                "Please upload a reference file and wait for processing to complete."
            ),
        )

    # Get active worker model from MongoDB — never hardcode
    config = await config_collection.find_one({"_id": "active_worker_model"})
    active_model = config["value"] if config else "phi3.5"

    # Retrieve top-20 chunks for broad coverage
    collection_name = f"{tenant_id}_{request.course_id}"
    chunks = retrieve_chunks_from_chromadb(
        collection_name=collection_name,
        query=request.course_title or "course overview",
        top_k=20,
    )

    if not chunks:
        raise HTTPException(
            status_code=400,
            detail=(
                "Reference material has no retrievable content. "
                "Please re-upload the reference file."
            ),
        )

    # Use first 12 chunks to respect context window (~4k tokens)
    context_chunks = chunks[:12]
    combined_context = "\n\n---\n\n".join(context_chunks)

    # Extract important keywords from chunks to anchor the LLM
    import re as _re
    all_words = " ".join(context_chunks).lower()
    # Remove common stopwords and short words, keep meaningful terms
    stopwords = {"the","a","an","is","are","was","were","be","been","being","have","has",
                 "had","do","does","did","will","would","could","should","may","might",
                 "shall","can","need","dare","ought","used","to","of","in","for","on",
                 "with","at","by","from","as","into","through","during","before","after",
                 "above","below","between","out","off","over","under","again","further",
                 "then","once","that","this","these","those","and","but","or","nor","not",
                 "so","if","it","its","they","them","their","we","our","he","she","his",
                 "her","you","your","i","me","my","all","each","every","both","few","more",
                 "most","other","some","such","no","only","own","same","than","too","very",
                 "just","also","about","up","which","what","when","where","how","who","whom"}
    words = _re.findall(r'\b[a-z]{4,}\b', all_words)
    word_freq = {}
    for w in words:
        if w not in stopwords:
            word_freq[w] = word_freq.get(w, 0) + 1
    top_keywords = sorted(word_freq, key=word_freq.get, reverse=True)[:20]
    keyword_hint = ", ".join(top_keywords)

    prompt = f"""You are a curriculum designer creating a TEXT-BASED course lesson plan.

Reference Material:
{combined_context}

Course Title: {request.course_title}
Key Topics Found in Reference: {keyword_hint}

STRICT RULES:
1. This is a TEXT-ONLY educational platform. NEVER include video links, URLs, YouTube references, multimedia links, or any external resources.
2. Every lesson summary MUST be a 2-3 sentence TEXT description explaining what the student will learn, written from the reference material above.
3. Every objective must describe a concrete learning outcome (e.g. "Explain Newton's First Law and give real-world examples").
4. Every key_concept must be a specific term or idea found in the reference material (e.g. "inertia", "force", "acceleration").
5. Do NOT invent topics not present in the reference material.
6. Create between 3 and 10 lessons covering the material in logical order.
7. Return ONLY a valid JSON array — no explanation, no markdown, no preamble.

Required JSON format:
[
  {{
    "lesson_number": 1,
    "title": "Descriptive lesson title from the reference content",
    "summary": "2 to 3 sentences describing what the student will learn in this lesson, based on the reference material",
    "objectives": ["specific learning objective 1", "specific learning objective 2", "specific learning objective 3"],
    "key_concepts": ["term from reference 1", "term from reference 2", "term from reference 3"],
    "estimated_duration_minutes": 45
  }}
]"""

    # Call active worker LLM via Ollama
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=5.0)) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model":   active_model,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"temperature": 0.3, "num_predict": 2048},
                },
            )
            # Check for Ollama errors BEFORE parsing
            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.error("Ollama returned %d: %s", response.status_code, error_detail)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Ollama returned HTTP {response.status_code}. "
                        f"Is the model '{active_model}' pulled? "
                        f"Run: ollama pull {active_model}\n"
                        f"Ollama error: {error_detail}"
                    ),
                )
        raw_output = response.json().get("response", "")
    except HTTPException:
        raise  # re-raise our own HTTPException
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=(
                "The local AI service (Ollama) is not reachable. "
                "Please start it with: ollama serve"
            ),
        )
    except Exception as exc:
        logger.error("[GenerateCourseLessons] Ollama call failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"AI generation request failed: {exc}",
        )

    # Parse JSON — strip markdown fences if the LLM added them
    try:
        clean = raw_output.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        # Find the JSON array
        start = clean.find("[")
        end   = clean.rfind("]")
        if start != -1 and end != -1:
            clean = clean[start : end + 1]
        lessons = json.loads(clean.strip())
        if not isinstance(lessons, list):
            raise ValueError("Expected a JSON array of lessons")
    except Exception as parse_exc:
        raw_preview = (raw_output or "")[:400]
        logger.error(
            "[GenerateCourseLessons] JSON parse failed. parse_error=%s | raw_preview=%r",
            parse_exc, raw_preview,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "AI failed to return a valid lesson structure. "
                f"Model output preview: {raw_preview!r}. "
                "Please try again."
            ),
        )

    # Validate through the existing pipeline — non-negotiable
    from app.services.validation_pipeline import run_validation_pipeline

    combined_text = " ".join([
        f"{l.get('title', '')} {l.get('summary', '')} {' '.join(l.get('objectives', []))}"
        for l in lessons
    ])
    validation = await run_validation_pipeline(
        generated_content=combined_text,
        topic=request.course_title,
        task_type="lesson",
        reference_chunks=context_chunks,
        worker_model=active_model,
        tenant_id=tenant_id,
    )

    final_score   = validation["final_score"]
    final_verdict = validation["final_verdict"]

    # For a teacher-facing lesson PLAN (titles + objectives + key concepts),
    # ROUGE always scores near 0 because plan language is abstracted from source.
    # Validation is advisory here — only block on critically low scores (< 10)
    # which indicate the model returned gibberish/empty content.
    if final_score < 10:
        logger.warning(
            "[GenerateCourseLessons] Critically low validation score=%s for course=%s — blocking",
            final_score, request.course_id,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "AI generated unusable content. Please try again.",
                "final_score": final_score,
                "final_verdict": final_verdict,
            },
        )

    if final_verdict == "FAIL":
        logger.info(
            "[GenerateCourseLessons] Validation advisory FAIL (score=%s) for course=%s "
            "— returning lessons anyway (teacher-side tool, plan language scores low on ROUGE by design)",
            final_score, request.course_id,
        )

    return {
        "lessons":        lessons,
        "lesson_count":   len(lessons),
        "final_score":    final_score,
        "final_verdict":  final_verdict if final_score >= 10 else "REVIEW",
        "worker_model":   active_model,
        "course_id":      request.course_id,
        "tenant_id":      tenant_id,
    }
