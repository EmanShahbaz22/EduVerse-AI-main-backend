"""
validation_pipeline.py — Combined Layer 1 + Layer 2 Validation

Orchestrates the full 2-layer validation pipeline for every AI generation:

    Layer 1 (40 pts): ROUGE, BERTScore, word count, structure
    Layer 2 (60 pts): RAG cosine similarity grounding (zero LLM)
    ─────────────────────────────────────────────────────────
    Final (100 pts):  >= 75 → PASS | 50-74 → REVIEW | < 50 → FAIL

On FAIL → regenerate once → if still FAIL → flag Super Admin via MongoDB.
Saves every attempt to `validationResults` collection for SA dashboard.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

logger = logging.getLogger(__name__)

# Score thresholds (per spec)
FINAL_PASS   = 75
FINAL_REVIEW = 50
LAYER1_PASS  = 25


async def run_validation_pipeline(
    *,
    generated_content: str,
    topic: str,
    task_type: Literal["lesson", "mcq", "tutor"],
    reference_chunks: list[str],
    regenerate_fn: Callable[[], Coroutine[Any, Any, str]] | None = None,
    # Metadata for MongoDB audit log
    worker_model: str = "unknown",
    student_id: str = "",
    tenant_id: str = "",
) -> dict:
    """
    Run the full 2-layer validation pipeline.

    Args:
        generated_content: The AI-generated text to validate.
        topic:             Topic/title for ROUGE/BERT relevance.
        task_type:         "lesson", "mcq", or "tutor".
        reference_chunks:  Top-k chunks from ChromaDB for Layer 2 grounding.
        regenerate_fn:     Async callable that returns a fresh generation attempt.
                           If None, regeneration is skipped on FAIL.
        worker_model:      Active Ollama model name (for audit log).
        student_id:        Student ID (for audit log).
        tenant_id:         Tenant ID (for audit log).

    Returns:
        {
            content,          ← final content to serve (may be 1st or 2nd attempt)
            final_score,
            final_verdict,    ← "PASS" | "REVIEW" | "FAIL"
            layer1,           ← full Layer 1 result dict
            layer2_rag,       ← full Layer 2 result dict
            attempts,         ← 1 or 2
            flagged_admin,    ← True if FAIL after 2 attempts
        }
    """
    t_start = time.time()

    # ── Attempt 1 ─────────────────────────────────────────────────────────────
    result1 = _validate_once(generated_content, topic, task_type, reference_chunks)
    verdict1 = result1["final_verdict"]

    if verdict1 in ("PASS", "REVIEW"):
        # Good enough — save and return
        latency = int((time.time() - t_start) * 1000)
        await _save_validation_result(
            result=result1,
            content=generated_content,
            topic=topic,
            task_type=task_type,
            worker_model=worker_model,
            student_id=student_id,
            tenant_id=tenant_id,
            latency_ms=latency,
            attempt=1,
        )
        return {**result1, "content": generated_content, "attempts": 1, "flagged_admin": False}

    # ── FAIL on attempt 1 — try to regenerate ─────────────────────────────────
    logger.warning("Validation FAIL on attempt 1 (score=%d) — regenerating...", result1["final_score"])

    if regenerate_fn is not None:
        try:
            new_content = await regenerate_fn()
            result2 = _validate_once(new_content, topic, task_type, reference_chunks)
            verdict2 = result2["final_verdict"]

            latency = int((time.time() - t_start) * 1000)
            flagged = verdict2 == "FAIL"

            await _save_validation_result(
                result=result2,
                content=new_content,
                topic=topic,
                task_type=task_type,
                worker_model=worker_model,
                student_id=student_id,
                tenant_id=tenant_id,
                latency_ms=latency,
                attempt=2,
                flagged_admin=flagged,
            )

            if flagged:
                await _flag_super_admin(topic, task_type, result2["final_score"], worker_model, tenant_id)

            return {**result2, "content": new_content, "attempts": 2, "flagged_admin": flagged}

        except Exception as e:
            logger.error("Regeneration attempt failed: %s", e)
            # Fall through — serve original content with REVIEW verdict
    else:
        logger.info("No regenerate_fn provided — serving FAIL content without retry")

    # No regeneration or regeneration errored → serve original, flag admin
    latency = int((time.time() - t_start) * 1000)
    await _save_validation_result(
        result=result1,
        content=generated_content,
        topic=topic,
        task_type=task_type,
        worker_model=worker_model,
        student_id=student_id,
        tenant_id=tenant_id,
        latency_ms=latency,
        attempt=1,
        flagged_admin=True,
    )
    await _flag_super_admin(topic, task_type, result1["final_score"], worker_model, tenant_id)
    return {**result1, "content": generated_content, "attempts": 1, "flagged_admin": True}


def _validate_once(
    content: str,
    topic: str,
    task_type: str,
    reference_chunks: list[str],
) -> dict:
    """Run Layer 1 + Layer 2 and compute final score."""
    from app.services.layer1_validation import check_layer1
    from app.services.rag_validator import check_layer2

    layer1 = check_layer1(content, topic, task_type)  # type: ignore
    layer2 = check_layer2(content, reference_chunks)

    final_score = layer1["layer1_total"] + layer2["layer2_pts"]

    if final_score >= FINAL_PASS:
        verdict = "PASS"
    elif final_score >= FINAL_REVIEW:
        verdict = "REVIEW"
    else:
        verdict = "FAIL"

    logger.info(
        "Validation: L1=%d/40 + L2=%d/60 = %d/100 → %s",
        layer1["layer1_total"], layer2["layer2_pts"], final_score, verdict,
    )

    return {
        "final_score":   final_score,
        "final_verdict": verdict,
        "layer1":        layer1,
        "layer2_rag":    layer2,
    }


async def _save_validation_result(
    *,
    result: dict,
    content: str,
    topic: str,
    task_type: str,
    worker_model: str,
    student_id: str,
    tenant_id: str,
    latency_ms: int,
    attempt: int,
    flagged_admin: bool = False,
) -> None:
    """Persist validation result to MongoDB for the Super Admin dashboard."""
    try:
        from app.db.database import validation_results_collection
        doc = {
            "worker_model":    worker_model,
            "task_type":       task_type,
            "topic":           topic,
            "student_id":      student_id,
            "tenant_id":       tenant_id,
            "layer1":          result["layer1"],
            "layer2_rag":      result["layer2_rag"],
            "final_score":     result["final_score"],
            "final_verdict":   result["final_verdict"],
            "latency_ms":      latency_ms,
            "attempt":         attempt,
            "flagged_admin":   flagged_admin,
            "content_length":  len(content),
            "timestamp":       datetime.now(timezone.utc),
        }
        await validation_results_collection.insert_one(doc)
    except Exception as e:
        logger.warning("Could not save validation result to DB: %s", e)


async def _flag_super_admin(
    topic: str,
    task_type: str,
    score: int,
    model: str,
    tenant_id: str,
) -> None:
    """
    Create a Super Admin notification in the DB when generation fails twice.
    The SA dashboard polls this collection to show pending reviews.
    """
    try:
        from app.db.database import config_collection
        await config_collection.update_one(
            {"_id": "sa_notifications"},
            {"$push": {
                "alerts": {
                    "type":      "generation_fail",
                    "topic":     topic,
                    "task_type": task_type,
                    "score":     score,
                    "model":     model,
                    "tenant_id": tenant_id,
                    "at":        datetime.now(timezone.utc).isoformat(),
                }
            }},
            upsert=True,
        )
        logger.warning(
            "Super Admin flagged: generation FAIL after 2 attempts — topic='%s' model=%s score=%d",
            topic, model, score,
        )
    except Exception as e:
        logger.warning("Could not flag Super Admin: %s", e)
