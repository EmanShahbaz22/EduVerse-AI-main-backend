"""
model_manager.py — Super Admin Model Control Endpoints

Endpoints:
    GET  /admin/models/leaderboard          → live scores for all 3 worker models
    POST /admin/models/benchmark            → run 10 standard prompts (sequential)
    POST /admin/models/set-active           → body: { model_name: "phi3.5" }
    GET  /admin/models/{model_name}/stats   → individual model stats
    GET  /admin/validations                 → filterable validation results log
    GET  /admin/models/health               → Ollama server status + available models

All endpoints require super_admin role.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.db.database import (
    benchmark_results_collection,
    config_collection,
    validation_results_collection,
)
from app.services.ollama_service import WORKER_MODELS, set_active_model, check_ollama_health

logger = logging.getLogger(__name__)
validations_router = APIRouter(prefix="/admin", tags=["Super Admin â€” Model Control"])

router = APIRouter(prefix="/admin/models", tags=["Super Admin — Model Control"])


# ── Standard benchmark prompts (3 prompts × 3 models = 9 total Ollama calls) ──
# Reduced from 10 to 3 (one per task type) so the full benchmark completes
# in ~10 minutes instead of 30+ minutes on limited RAM.
BENCHMARK_PROMPTS = [
    {"task_type": "lesson", "topic": "Newton's Laws of Motion", "pace": "average"},
    {"task_type": "mcq",   "topic": "Python Programming Basics", "difficulty": "medium", "count": 3},
    {"task_type": "tutor", "question": "Explain recursion in simple words"},
]


# ── Schemas ───────────────────────────────────────────────────────────────────

class SetActiveModelRequest(BaseModel):
    model_name: str


# ── GET /admin/models/health ──────────────────────────────────────────────────

@router.get("/health", summary="Check Ollama server status and available models")
async def get_ollama_health(current_user: dict = Depends(get_current_user)):
    _require_superadmin(current_user)
    return await check_ollama_health()


# ── GET /admin/models/leaderboard ─────────────────────────────────────────────

@router.get("/leaderboard", summary="Live leaderboard scores for all worker models")
async def get_leaderboard(current_user: dict = Depends(get_current_user)):
    """
    Aggregate validation_results for each model and return live scores.
    Shows: avg_score, pass_rate, avg_layer1, avg_layer2, total_runs.
    """
    _require_superadmin(current_user)

    leaderboard = []
    active_model_doc = await config_collection.find_one({"_id": "active_worker_model"})
    active_model = active_model_doc["value"] if active_model_doc else "phi3.5"

    for model_name in WORKER_MODELS:
        pipeline = [
            {"$match": {"worker_model": model_name}},
            {"$group": {
                "_id":           "$worker_model",
                "avg_score":     {"$avg": "$final_score"},
                "pass_count":    {"$sum": {"$cond": [{"$eq": ["$final_verdict", "PASS"]},  1, 0]}},
                "review_count":  {"$sum": {"$cond": [{"$eq": ["$final_verdict", "REVIEW"]}, 1, 0]}},
                "fail_count":    {"$sum": {"$cond": [{"$eq": ["$final_verdict", "FAIL"]},  1, 0]}},
                "total_runs":    {"$sum": 1},
                "avg_l1":        {"$avg": "$layer1.layer1_total"},
                "avg_l2":        {"$avg": "$layer2_rag.layer2_pts"},
                "avg_latency":   {"$avg": "$latency_ms"},
            }},
        ]
        cursor = validation_results_collection.aggregate(pipeline)
        row = None
        async for doc in cursor:
            row = doc

        if row:
            total = row["total_runs"]
            leaderboard.append({
                "model":        model_name,
                "is_active":    model_name == active_model,
                "avg_score":    round(row["avg_score"], 1),
                "pass_rate":    round(row["pass_count"] / total * 100, 1) if total else 0,
                "review_rate":  round(row["review_count"] / total * 100, 1) if total else 0,
                "fail_rate":    round(row["fail_count"] / total * 100, 1) if total else 0,
                "total_runs":   total,
                "avg_layer1":   round(row["avg_l1"] or 0, 1),
                "avg_layer2":   round(row["avg_l2"] or 0, 1),
                "avg_latency_ms": int(row["avg_latency"] or 0),
            })
        else:
            leaderboard.append({
                "model":        model_name,
                "is_active":    model_name == active_model,
                "avg_score":    0,
                "pass_rate":    0,
                "review_rate":  0,
                "fail_rate":    0,
                "total_runs":   0,
                "avg_layer1":   0,
                "avg_layer2":   0,
                "avg_latency_ms": 0,
                "note":         "No validation data yet — run benchmark first",
            })

    leaderboard.sort(key=lambda x: x["avg_score"], reverse=True)
    return {"active_model": active_model, "leaderboard": leaderboard}


# ── POST /admin/models/set-active ─────────────────────────────────────────────

@router.post("/set-active", summary="Set the active worker model")
async def set_active(
    request: SetActiveModelRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_superadmin(current_user)

    if request.model_name not in WORKER_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model '{request.model_name}'. Available: {list(WORKER_MODELS.keys())}"
        )

    user_id = current_user.get("user_id") or current_user.get("id", "superadmin")
    await set_active_model(request.model_name, updated_by=user_id)
    return {"message": f"Active model set to '{request.model_name}'", "model": request.model_name}


# ── POST /admin/models/benchmark ──────────────────────────────────────────────

@router.post("/benchmark", summary="Run benchmark across all 3 worker models (sequential)")
async def run_benchmark(current_user: dict = Depends(get_current_user)):
    """
    Runs 3 standard benchmark prompts (1 lesson, 1 MCQ, 1 tutor) on each of
    the 3 worker models SEQUENTIALLY — 9 total Ollama calls.
    Each model is tested with a direct model_override so results reflect
    that specific model, not the current active model.
    Results are saved to benchmarkResults collection and returned.

    WARNING: Requires all 3 models to be pulled via `ollama pull`.
    Never run in parallel — OOM risk on limited RAM.
    """
    _require_superadmin(current_user)

    logger.info("Benchmark started by %s", current_user.get("email", "unknown"))

    from app.services.ollama_service import run_model_benchmark
    results = await run_model_benchmark(BENCHMARK_PROMPTS)

    # Save to MongoDB
    doc = {
        "run_by":  current_user.get("user_id") or current_user.get("id", ""),
        "run_at":  datetime.now(timezone.utc),
        "results": results,
    }
    await benchmark_results_collection.insert_one(doc)
    doc["_id"] = str(doc.get("_id", ""))

    return {"message": "Benchmark complete", "results": results}


# ── GET /admin/models/{model_name}/stats ─────────────────────────────────────

@router.get("/{model_name}/stats", summary="Individual model validation stats")
async def get_model_stats(
    model_name: str,
    limit: int  = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    _require_superadmin(current_user)

    if model_name not in WORKER_MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_name}")

    cursor = (
        validation_results_collection
        .find({"worker_model": model_name})
        .sort("timestamp", -1)
        .limit(limit)
    )
    records = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        if isinstance(doc.get("timestamp"), datetime):
            doc["timestamp"] = doc["timestamp"].isoformat()
        records.append(doc)

    return {"model": model_name, "count": len(records), "records": records}


# ── GET /admin/validations ────────────────────────────────────────────────────

@router.get(
    "/validations",
    summary="Filter validation results by model/verdict",
    tags=["Super Admin — Model Control"],
)
async def get_validations(
    model:   str | None = Query(None),
    verdict: str | None = Query(None, description="PASS | REVIEW | FAIL"),
    limit:   int        = Query(50, ge=1, le=200),
    current_user: dict  = Depends(get_current_user),
):
    _require_superadmin(current_user)

    query: dict = {}
    if model:
        query["worker_model"] = model
    if verdict:
        query["final_verdict"] = verdict.upper()

    cursor = (
        validation_results_collection
        .find(query)
        .sort("timestamp", -1)
        .limit(limit)
    )
    records = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        if isinstance(doc.get("timestamp"), datetime):
            doc["timestamp"] = doc["timestamp"].isoformat()
        records.append(doc)

    return {"count": len(records), "records": records}


@validations_router.get(
    "/validations",
    summary="Filter validation results by model/verdict",
    tags=["Super Admin â€” Model Control"],
)
async def get_validations_alias(
    model: str | None = Query(None),
    verdict: str | None = Query(None, description="PASS | REVIEW | FAIL"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    return await get_validations(
        model=model,
        verdict=verdict,
        limit=limit,
        current_user=current_user,
    )


# ── SA Notifications ──────────────────────────────────────────────────────────

@router.get("/notifications", summary="Get Super Admin alerts (failed validations)")
async def get_notifications(current_user: dict = Depends(get_current_user)):
    _require_superadmin(current_user)
    doc = await config_collection.find_one({"_id": "sa_notifications"})
    alerts = doc.get("alerts", []) if doc else []
    return {"count": len(alerts), "alerts": list(reversed(alerts[-50:]))}


@router.delete("/notifications", summary="Clear Super Admin alerts")
async def clear_notifications(current_user: dict = Depends(get_current_user)):
    _require_superadmin(current_user)
    await config_collection.update_one(
        {"_id": "sa_notifications"},
        {"$set": {"alerts": []}},
        upsert=True,
    )
    return {"message": "Notifications cleared."}


# ── Helper ────────────────────────────────────────────────────────────────────

def _require_superadmin(user: dict) -> None:
    """Raise 403 if the user is not a super_admin."""
    role = user.get("role", "")
    if role not in ("super_admin", "superadmin"):
        raise HTTPException(
            status_code=403,
            detail="Super Admin access required."
        )
