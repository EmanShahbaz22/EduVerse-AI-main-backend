"""
rag_service.py — LangChain RAG Pipeline

Responsibilities:
    1. load_and_chunk(file_path, file_type)         → List[Document]
    2. embed_and_store(chunks, collection_id)        → None
    3. retrieve_context(query, collection_id, top_k) → str
    4. generate_with_rag(prompt_cfg, collection_id)  → str

Tech:
    - Loaders: PyPDFLoader / UnstructuredPPTXLoader / Docx2txtLoader
    - Splitter: RecursiveCharacterTextSplitter(size=500, overlap=50)
    - Embeddings: all-MiniLM-L6-v2 (HuggingFaceEmbeddings, ~90MB, local)
    - Vector DB: ChromaDB persisted at ./chroma_db
    - LLM: active worker via ollama_service (prompt: "use ONLY reference material")

Critical constants (per spec):
    CHUNK_SIZE    = 500
    CHUNK_OVERLAP = 50
    TOP_K         = 5
    CHROMA_PATH   = "./chroma_db"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CHUNK_SIZE    = 500
CHUNK_OVERLAP = 50
TOP_K         = 5
CHROMA_PATH   = "./chroma_db"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

FileType = Literal["pdf", "pptx", "docx"]


# ── Priority 2: File-size-aware chunk configuration ───────────────────────────

def get_chunk_config(file_size_mb: float) -> dict:
    """
    Return chunk_size and chunk_overlap based on file size.

    Larger files need larger chunks so the LLM gets better coverage per
    retrieval without exceeding its context window.

    | File Size     | Chunk Size | Overlap | Reason                      |
    |---------------|------------|---------|-----------------------------||
    | < 2 MB        | 500 chars  | 50      | Fine-grained detail         |
    | 2 – 10 MB     | 800 chars  | 100     | Balance detail and coverage |
    | 10 – 25 MB    | 1200 chars | 150     | Broad coverage per chunk    |
    | > 25 MB       | 1500 chars | 200     | Maximum coverage            |
    """
    if file_size_mb < 2:
        return {"chunk_size": 500, "chunk_overlap": 50}
    elif file_size_mb < 10:
        return {"chunk_size": 800, "chunk_overlap": 100}
    elif file_size_mb < 25:
        return {"chunk_size": 1200, "chunk_overlap": 150}
    else:
        return {"chunk_size": 1500, "chunk_overlap": 200}


def load_and_chunk_document(
    file_bytes: bytes,
    file_extension: str,
    file_size_mb: float,
    tenant_id: str,
    course_id: str,
) -> list:
    """
    Load a document from raw bytes and split into size-aware chunks.

    Used by the background chunking task so we don't need to re-read the
    already-saved file from disk (the bytes are already in memory after upload).

    Args:
        file_bytes:     Raw file bytes.
        file_extension: One of "pdf", "pptx", "docx" (no leading dot).
        file_size_mb:   File size in MB — used to select chunk config.
        tenant_id:      Tenant ID (stored in chunk metadata).
        course_id:      Course ID (stored in chunk metadata).

    Returns:
        List of LangChain Document objects (chunks).
    """
    import os as _os
    import tempfile as _tempfile
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    # Write bytes to a temp file so loaders (which expect a file path) can read it.
    with _tempfile.NamedTemporaryFile(
        suffix=f".{file_extension}", delete=False
    ) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if file_extension == "pdf":
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(tmp_path)
        elif file_extension == "pptx":
            from langchain_community.document_loaders import UnstructuredPPTXLoader
            loader = UnstructuredPPTXLoader(tmp_path)
        elif file_extension == "docx":
            from langchain_community.document_loaders import Docx2txtLoader
            loader = Docx2txtLoader(tmp_path)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}")

        documents = loader.load()

        # Stamp metadata on every document page before splitting
        for doc in documents:
            doc.metadata.update({
                "tenant_id": tenant_id,
                "course_id": course_id,
            })

        config = get_chunk_config(file_size_mb)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=config["chunk_size"],
            chunk_overlap=config["chunk_overlap"],
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_documents(documents)

        logger.info(
            "load_and_chunk_document: %d chunks (size=%d overlap=%d) from %.2f MB %s file",
            len(chunks),
            config["chunk_size"],
            config["chunk_overlap"],
            file_size_mb,
            file_extension,
        )
        return chunks

    finally:
        # Always clean up the temp file — even if loading fails
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass


# ── Lazy imports (heavy deps — only loaded when RAG is used) ───────────────────

def _get_embeddings():
    """Return HuggingFaceEmbeddings with all-MiniLM-L6-v2 (cached after first load)."""
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def _get_chroma(collection_id: str, embeddings=None):
    """Return a persistent ChromaDB vector store for the given collection."""
    from langchain_community.vectorstores import Chroma
    if embeddings is None:
        embeddings = _get_embeddings()
    persist_dir = os.path.join(CHROMA_PATH, collection_id)
    return Chroma(
        collection_name=collection_id,
        embedding_function=embeddings,
        persist_directory=persist_dir,
    )


# ── 1. Document Loading ────────────────────────────────────────────────────────

def load_and_chunk(file_path: str, file_type: FileType) -> list:
    """
    Load a teacher-uploaded file and split it into chunks.

    Args:
        file_path: Absolute path to the uploaded file.
        file_type: One of "pdf", "pptx", "docx".

    Returns:
        List of LangChain Document objects (chunks).
    """
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    logger.info("Loading file: %s (type=%s)", file_path, file_type)

    # Select appropriate loader
    if file_type == "pdf":
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(file_path)
    elif file_type == "pptx":
        from langchain_community.document_loaders import UnstructuredPPTXLoader
        loader = UnstructuredPPTXLoader(file_path)
    elif file_type == "docx":
        from langchain_community.document_loaders import Docx2txtLoader
        loader = Docx2txtLoader(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_type}. Use pdf, pptx, or docx.")

    documents = loader.load()
    logger.info("Loaded %d pages/sections from %s", len(documents), Path(file_path).name)

    # Split into chunks
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    logger.info("Split into %d chunks (size=%d, overlap=%d)", len(chunks), CHUNK_SIZE, CHUNK_OVERLAP)
    return chunks


# ── 2. Embed + Store ───────────────────────────────────────────────────────────

def embed_and_store(chunks: list, collection_id: str) -> int:
    """
    Embed document chunks and store them in ChromaDB.

    Args:
        chunks:        List of Document objects from load_and_chunk().
        collection_id: Unique ID for this upload's ChromaDB collection.
                       Typically: f"{course_id}_{lesson_id}" or upload_id.

    Returns:
        Number of chunks stored.
    """
    if not chunks:
        logger.warning("No chunks to embed for collection %s", collection_id)
        return 0

    logger.info("Embedding %d chunks → ChromaDB collection '%s'", len(chunks), collection_id)
    embeddings = _get_embeddings()

    from langchain_community.vectorstores import Chroma
    persist_dir = os.path.join(CHROMA_PATH, collection_id)
    os.makedirs(persist_dir, exist_ok=True)

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=collection_id,
        persist_directory=persist_dir,
    )
    logger.info("Stored %d chunks in ChromaDB at %s", len(chunks), persist_dir)
    return len(chunks)


# ── 3. Context Retrieval ───────────────────────────────────────────────────────

def retrieve_context(query: str, collection_id: str, top_k: int = TOP_K) -> str:
    """
    Retrieve the top-k most relevant chunks for a query from ChromaDB.

    Args:
        query:         The student's question or lesson topic.
        collection_id: ChromaDB collection to search.
        top_k:         Number of chunks to retrieve (default: 5).

    Returns:
        Concatenated text of the top-k chunks, or empty string if no store found.
    """
    persist_dir = os.path.join(CHROMA_PATH, collection_id)
    if not os.path.exists(persist_dir):
        logger.info("No ChromaDB store found for collection '%s' — skipping RAG retrieval", collection_id)
        return ""

    try:
        vectorstore = _get_chroma(collection_id)
        docs = vectorstore.similarity_search(query, k=top_k)
        context = "\n\n".join(doc.page_content for doc in docs)
        logger.info("Retrieved %d chunks for query: '%s...'", len(docs), query[:60])
        return context
    except Exception as e:
        logger.warning("ChromaDB retrieval failed for collection '%s': %s", collection_id, e)
        return ""


# ── 4. RAG-Grounded Generation ─────────────────────────────────────────────────

async def generate_with_rag(
    task_type: Literal["lesson", "base_lesson", "quiz", "tutor"],
    topic: str,
    collection_id: str | None,
    *,
    pace: str = "average",
    score: float = 70.0,
    weak_areas: str = "",
    source_content: str = "",
    difficulty: str = "medium",
    count: int = 5,
    message: str = "",
) -> str:
    """
    Generate content using RAG context from ChromaDB + active worker LLM.

    The prompt instructs the LLM to use ONLY the reference material.
    If no collection_id or no ChromaDB store exists, falls back to
    topic-only generation (same as non-RAG path).

    Returns:
        Raw LLM output string (unparsed — caller parses).
    """
    from app.services.ollama_service import _call_ollama, get_active_model

    # Retrieve relevant context
    context = ""
    if collection_id:
        context = retrieve_context(topic if not message else message, collection_id)

    model = await get_active_model()
    logger.info("RAG generation: task=%s model=%s has_context=%s", task_type, model, bool(context))

    if task_type == "lesson" or task_type == "base_lesson":
        prompt = _build_lesson_rag_prompt(topic, pace, score, weak_areas, source_content, context)
    elif task_type == "quiz":
        prompt = _build_quiz_rag_prompt(topic, difficulty, count, context)
    elif task_type == "tutor":
        prompt = _build_tutor_rag_prompt(message, context)
    else:
        raise ValueError(f"Unknown task_type: {task_type}")

    return await _call_ollama(prompt, model=model)


def _build_lesson_rag_prompt(topic, pace, score, weak_areas, source_content, context):
    ctx_section = f"""
Reference Material (use ONLY this material for factual claims):
---
{context}
---
""" if context else ""

    source_section = f"""
Teacher Notes:
{source_content}
""" if source_content else ""

    return f"""\
You are an expert educational content creator.
Generate a personalized lesson. Use ONLY the reference material for facts.
{ctx_section}{source_section}
Lesson Topic: {topic}
Student Pace: {pace}
Quiz Score: {score}%
Weak Areas: {weak_areas or "general review"}

Return ONLY valid JSON:
{{
  "title": "string",
  "content": "full lesson in Markdown",
  "difficulty": "beginner|intermediate|advanced",
  "estimated_duration_minutes": integer,
  "key_concepts": ["concept1", "concept2"],
  "summary": "2-3 sentence summary"
}}
"""


def _build_quiz_rag_prompt(topic, difficulty, count, context):
    ctx_section = f"""
Reference Material (base all questions on this material ONLY):
---
{context}
---
""" if context else ""

    return f"""\
You are an expert MCQ quiz creator.
{ctx_section}
Topic: {topic}
Difficulty: {difficulty}
Number of Questions: {count}

Return ONLY valid JSON:
{{
  "title": "Quiz: {topic}",
  "topic": "{topic}",
  "difficulty": "{difficulty}",
  "questions": [
    {{
      "question": "string",
      "options": ["A", "B", "C", "D"],
      "correct_answer": "A",
      "explanation": "Why A is correct"
    }}
  ]
}}
"""


def _build_tutor_rag_prompt(message, context):
    ctx_section = f"""
Course Material (answer using this material ONLY):
---
{context}
---
""" if context else ""

    return f"""\
You are a helpful AI study assistant for students.
{ctx_section}
Student Question: {message}

Provide a clear, encouraging answer. If the answer is not in the material, say so.
"""


# ── Auto-description generator (for teacher lesson form) ─────────────────────

async def generate_lesson_description(upload_id: str, lesson_title: str) -> dict:
    """
    Use RAG to auto-fill the teacher's lesson description form.
    Called after a teacher uploads a reference file.

    Returns dict with: objectives, key_concepts, summary
    """
    import json as _json
    from app.services.ollama_service import _call_ollama, get_active_model

    context = retrieve_context(lesson_title, upload_id, top_k=5)
    if not context:
        return {"objectives": [], "key_concepts": [], "summary": ""}

    model = await get_active_model()
    prompt = f"""\
You are an expert curriculum designer.
Based on the following reference material, generate a lesson description.

Reference Material:
---
{context}
---

Lesson Title: {lesson_title}

Return ONLY valid JSON:
{{
  "objectives": ["students will be able to...", "..."],
  "key_concepts": ["concept1", "concept2", "concept3"],
  "summary": "2-3 sentence description of what this lesson covers"
}}
"""
    raw = await _call_ollama(prompt, model=model)

    # Parse
    try:
        repaired = raw.strip()
        repaired = repaired[repaired.find('{'):repaired.rfind('}')+1]
        return _json.loads(repaired)
    except Exception:
        return {"objectives": [], "key_concepts": [], "summary": raw[:300]}


# ── Collection management ──────────────────────────────────────────────────────




def collection_exists(collection_id: str) -> bool:
    """Check if a ChromaDB collection exists for this upload."""
    persist_dir = os.path.join(CHROMA_PATH, collection_id)
    return os.path.exists(persist_dir)


# ── Spec-compatible public API aliases ────────────────────────────────────────
# The audit spec requires specific function signatures.  The core logic is
# implemented above under different names (load_and_chunk / embed_and_store /
# retrieve_context / generate_with_rag).  The wrappers below satisfy the spec
# while leaving all existing callers untouched.

def load_document(file_path: str, file_type: FileType) -> list:
    """
    Spec alias for load_and_chunk() — step 1 only (loading).
    Returns list of LangChain Document objects (un-chunked pages/sections).
    """
    import logging as _logging
    from pathlib import Path as _Path
    _log = _logging.getLogger(__name__)
    _log.info("load_document: %s (type=%s)", file_path, file_type)

    if file_type == "pdf":
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(file_path)
    elif file_type == "pptx":
        from langchain_community.document_loaders import UnstructuredPPTXLoader
        loader = UnstructuredPPTXLoader(file_path)
    elif file_type == "docx":
        from langchain_community.document_loaders import Docx2txtLoader
        loader = Docx2txtLoader(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_type}. Use pdf, pptx, or docx.")

    try:
        documents = loader.load()
        _log.info("Loaded %d page(s)/section(s) from %s", len(documents), _Path(file_path).name)
        return documents
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")


def chunk_documents(documents: list) -> list:
    """
    Spec alias — splits a list of Documents into chunks.
    chunk_size=500, chunk_overlap=50, separators=[\"\\n\\n\",\"\\n\",\".\",\" \",\"\"]
    """
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    logger.info("chunk_documents: %d chunks (size=%d overlap=%d)", len(chunks), CHUNK_SIZE, CHUNK_OVERLAP)
    return chunks


def store_chunks_in_chromadb(
    chunks: list,
    tenant_id: str,
    course_id: str,
    lesson_id: str = "",
    scope: str = "course",
    chapter_tag: str = "",
) -> object:
    """
    Store document chunks into ChromaDB using COURSE-LEVEL collection scoping.

    Collection key: f"{tenant_id}_{course_id}"

    CRITICAL: The collection is ALWAYS course-level, never lesson-level.
    Lesson IDs do not exist at upload time. All lessons for a course share
    the same reference material in the same ChromaDB collection.

    Args:
        chunks:      List of Document objects from load_and_chunk_document().
        tenant_id:   Tenant identifier.
        course_id:   Course identifier.
        lesson_id:   Kept for backwards compat — stored in metadata only.
        scope:       Kept for backwards compat — stored in metadata only.
        chapter_tag: Optional chapter/section name from the teacher.

    Returns:
        The Chroma vectorstore object.
    """
    from langchain_community.vectorstores import Chroma

    # CORRECT: course-level collection — never lesson-level
    collection_name = f"{tenant_id}_{course_id}"
    persist_dir = os.path.join(CHROMA_PATH, collection_name)
    os.makedirs(persist_dir, exist_ok=True)

    # Stamp metadata on every chunk
    for chunk in chunks:
        chunk.metadata.update({
            "tenant_id":   tenant_id,
            "course_id":   course_id,
            "lesson_id":   lesson_id,
            "scope":       scope,
            "chapter_tag": chapter_tag,
        })

    embeddings = _get_embeddings()
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=persist_dir,
    )
    # Explicit persist() for spec compliance (no-op on newer ChromaDB but harmless)
    try:
        vectorstore.persist()
    except AttributeError:
        pass  # ChromaDB >= 0.4 auto-persists; persist() removed from public API

    logger.info(
        "store_chunks_in_chromadb: %d chunks → collection '%s' at %s",
        len(chunks), collection_name, persist_dir,
    )
    return vectorstore


def retrieve_chunks_from_chromadb(
    collection_name: str,
    query: str,
    top_k: int = TOP_K,
) -> list[str]:
    """
    Retrieve the top-k most relevant chunk texts from a ChromaDB collection.

    Used by the generate-course-lessons endpoint which operates at course level.
    Returns plain text strings (not Document objects) for easy LLM prompting.

    Args:
        collection_name: e.g. f"{tenant_id}_{course_id}" — ALWAYS course-level.
        query:           The course title or topic to search by.
        top_k:           Maximum number of chunks to retrieve.

    Returns:
        List of chunk text strings. Empty list if collection does not exist.
    """
    persist_dir = os.path.join(CHROMA_PATH, collection_name)
    if not os.path.exists(persist_dir):
        logger.info(
            "retrieve_chunks_from_chromadb: no collection found at '%s'",
            persist_dir,
        )
        return []

    try:
        vectorstore = _get_chroma(collection_name)
        docs = vectorstore.similarity_search(query, k=top_k)
        texts = [doc.page_content for doc in docs]
        logger.info(
            "retrieve_chunks_from_chromadb: retrieved %d chunks for query '%s...' from '%s'",
            len(texts), query[:60], collection_name,
        )
        return texts
    except Exception as exc:
        logger.warning(
            "retrieve_chunks_from_chromadb failed for collection '%s': %s",
            collection_name, exc,
        )
        return []


def retrieve_chunks(
    query: str,
    tenant_id: str,
    course_id: str,
    lesson_id: str | None = None,
    top_k: int = TOP_K,
) -> list[str]:
    """
    Spec-compatible retrieval.
    Filters by lesson_id if provided, else course_id.
    Returns list of plain text strings (not Document objects).
    Falls back to empty list if collection does not exist.
    """
    collection_name = f"{tenant_id}_{course_id}"
    persist_dir = os.path.join(CHROMA_PATH, collection_name)
    legacy_collection_name = f"{course_id}_{lesson_id}" if lesson_id else f"{course_id}_course"
    legacy_persist_dir = os.path.join(CHROMA_PATH, legacy_collection_name)

    def _search_legacy_collection() -> list[str]:
        if not os.path.exists(legacy_persist_dir):
            return []

        try:
            vectorstore = _get_chroma(legacy_collection_name)
            docs = vectorstore.similarity_search(query, k=top_k)
            chunks = [doc.page_content for doc in docs]
            logger.info(
                "retrieve_chunks: %d chunks retrieved from legacy collection '%s' for query: '%s...'",
                len(chunks),
                legacy_collection_name,
                query[:60],
            )
            return chunks
        except Exception as e:
            logger.warning(
                "retrieve_chunks fallback failed for legacy collection '%s': %s",
                legacy_collection_name,
                e,
            )
            return []

    if not os.path.exists(persist_dir):
        legacy_chunks = _search_legacy_collection()
        if legacy_chunks:
            return legacy_chunks
        logger.info(
            "retrieve_chunks: no tenant collection for tenant '%s' and no legacy collection '%s' — returning []",
            tenant_id,
            legacy_collection_name,
        )
        return []

    try:
        vectorstore = _get_chroma(collection_name)
        filter_dict = (
            {"lesson_id": lesson_id} if lesson_id
            else {"course_id": course_id}
        )
        docs = vectorstore.similarity_search(query, k=top_k, filter=filter_dict)
        chunks = [doc.page_content for doc in docs]
        if not chunks:
            legacy_chunks = _search_legacy_collection()
            if legacy_chunks:
                return legacy_chunks
        logger.info("retrieve_chunks: %d chunks retrieved for query: '%s...'", len(chunks), query[:60])
        return chunks
    except Exception as e:
        logger.warning("retrieve_chunks failed for tenant '%s': %s", tenant_id, e)
        legacy_chunks = _search_legacy_collection()
        return legacy_chunks if legacy_chunks else []


async def generate_content_with_rag(
    topic: str,
    task_type: str,
    student_level: str,
    tenant_id: str,
    course_id: str,
    lesson_id: str,
    active_model_name: str,
) -> dict:
    """
    Spec-compatible generation function.
    task_type: \"lesson\" | \"mcq\" | \"tutor\"
    Returns dict with keys: \"content\", \"source_documents\"
    """
    from app.services.ollama_service import _call_ollama

    # Retrieve context
    reference_chunks = retrieve_chunks(topic, tenant_id, course_id, lesson_id)
    context = "\n\n".join(reference_chunks) if reference_chunks else ""
    ctx_section = f"\nReference Material (use ONLY this):\n---\n{context}\n---\n" if context else ""

    if task_type == "lesson":
        question = (
            f"{ctx_section}"
            f"Generate a lesson about '{topic}' for a {student_level} student in Markdown format "
            f"with a title (##), key concepts section, and a summary section."
        )
    elif task_type == "mcq":
        question = (
            f"{ctx_section}"
            f"Generate a JSON array of 5 MCQ questions about '{topic}' for a {student_level} student. "
            f"Each item must have: question, options (A-D list), correct_answer (A/B/C/D), explanation."
        )
    else:  # tutor
        question = (
            f"{ctx_section}"
            f"Explain '{topic}' clearly for a {student_level} student."
        )

    try:
        raw = await _call_ollama(question, model=active_model_name)
        return {"content": raw, "source_documents": reference_chunks}
    except Exception as e:
        logger.exception("generate_content_with_rag failed")
        raise


async def auto_generate_lesson_description(
    topic: str,
    tenant_id: str,
    course_id: str,
    lesson_id: str | None = None,
    active_model_name: str = "phi3.5",
) -> dict:
    """
    Spec-compatible lesson description generator.
    Returns dict with: lesson_title, overview, learning_objectives,
    key_concepts, difficulty_level, estimated_duration_minutes, prerequisite_topics.
    """
    import json as _json
    import re as _re
    from app.services.ollama_service import _call_ollama

    reference_chunks = retrieve_chunks(topic, tenant_id, course_id, lesson_id)
    context = "\n\n".join(reference_chunks) if reference_chunks else ""
    ctx_section = f"\nReference Material:\n---\n{context}\n---\n" if context else ""

    prompt = f"""\
You are an expert curriculum designer.{ctx_section}
Requested Topic: {topic}

Instructions:
- Use the reference material as the primary source of truth.
- If the reference clearly indicates a lesson title or topic, use that title instead of an unrelated requested topic.
- Do not introduce concepts, subjects, or terminology that are not supported by the reference material.
- Keep the description aligned with the uploaded course reference.

Return ONLY valid JSON with exactly these keys:
{{
  "lesson_title": "string",
  "overview": "string",
  "learning_objectives": ["string"],
  "key_concepts": ["string"],
  "difficulty_level": "beginner|intermediate|advanced",
  "estimated_duration_minutes": integer,
  "prerequisite_topics": ["string"]
}}
"""
    try:
        raw = await _call_ollama(prompt, model=active_model_name)
        # Primary parse
        repaired = raw.strip()
        repaired = repaired[repaired.find("{"):repaired.rfind("}") + 1]
        return _json.loads(repaired)
    except (_json.JSONDecodeError, ValueError):
        # Regex fallback
        try:
            match = _re.search(r"\{[\s\S]*\}", raw)
            if match:
                return _json.loads(match.group())
        except Exception:
            pass
        return {
            "lesson_title": topic,
            "overview": raw[:300] if raw else "Generation failed.",
            "learning_objectives": [],
            "key_concepts": [],
            "difficulty_level": "intermediate",
            "estimated_duration_minutes": 30,
            "prerequisite_topics": [],
            "error": "JSON parse failed — raw response stored in overview",
        }
    except Exception as e:
        logger.error("auto_generate_lesson_description failed: %s", e)
        return {
            "lesson_title": topic,
            "overview": "",
            "learning_objectives": [],
            "key_concepts": [],
            "difficulty_level": "intermediate",
            "estimated_duration_minutes": 30,
            "prerequisite_topics": [],
            "error": str(e),
        }


def delete_collection(collection_name: str) -> bool:
    """
    Deletes a ChromaDB collection from disk.
    Used when a teacher deletes a reference file.
    """
    import shutil
    persist_dir = os.path.join(CHROMA_PATH, collection_name)
    if os.path.exists(persist_dir):
        try:
            shutil.rmtree(persist_dir)
            logger.info("Deleted ChromaDB collection directory: %s", persist_dir)
            return True
        except Exception as e:
            logger.error("Failed to delete ChromaDB collection '%s': %s", collection_name, e)
            return False
    else:
        logger.info("ChromaDB collection directory not found for deletion: %s", persist_dir)
        return True
