"""
verify_fixes.py — EduVerse-AI 8-Fix Verification Script
Run from the backend root:  python verify_fixes.py
"""
import asyncio
import sys
import io

# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeError
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PASS = "[OK]"
FAIL = "[FAIL]"
results = []

def ok(msg):
    results.append((True, msg))
    print(f"  {PASS} {msg}")

def fail(msg, err=""):
    results.append((False, msg))
    print(f"  {FAIL} {msg}")
    if err:
        print(f"       ERROR: {err}")

# ─────────────────────────────────────────────────────────────
# FIX 1 — MongoDB seeding: _seed_active_worker_model in main.py
# ─────────────────────────────────────────────────────────────
print("\n[FIX 1] MongoDB startup seed (_seed_active_worker_model in main.py)")
try:
    import ast, pathlib
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert "_seed_active_worker_model" in fn_names, "function not found"
    assert "_seed_active_worker_model" in src and "insert_one" in src
    assert "await _seed_active_worker_model()" in src
    ok("_seed_active_worker_model defined and called in lifespan startup")
except Exception as e:
    fail("FIX 1 not applied", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 2 — rag_service.py spec-compatible public aliases
# ─────────────────────────────────────────────────────────────
print("\n[FIX 2] rag_service.py spec-compatible public function aliases")
try:
    from app.services.rag_service import (
        load_document,
        chunk_documents,
        store_chunks_in_chromadb,
        retrieve_chunks,
        generate_content_with_rag,
        auto_generate_lesson_description,
        load_and_chunk,
        embed_and_store,
        retrieve_context,
        collection_exists,
        delete_collection,
    )
    ok("All 6 spec aliases + 5 core functions imported OK from rag_service.py")
except ImportError as e:
    fail("FIX 2 import failure", str(e))
except Exception as e:
    fail("FIX 2 unexpected error", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 3 — layer1_validation.py spec-compatible public functions
# ─────────────────────────────────────────────────────────────
print("\n[FIX 3] layer1_validation.py spec-compatible public functions")
try:
    from app.services.layer1_validation import (
        check_layer1,
        check_rouge_relevance,
        check_bert_similarity,
        check_completeness,
        check_lesson_structure,
        check_mcq_structure,
        calculate_layer1_score,
    )
    ok("All 7 spec functions imported OK from layer1_validation.py")
    # Quick smoke test (no heavy deps needed)
    result = check_completeness("word " * 400, "lesson")
    assert result["passed"] is True, f"check_completeness failed: {result}"
    ok("check_completeness('lesson', 400 words) → passed=True")
except ImportError as e:
    fail("FIX 3 import failure", str(e))
except Exception as e:
    fail("FIX 3 smoke test failed", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 4 — rag_validator.py validate_with_rag alias
# ─────────────────────────────────────────────────────────────
print("\n[FIX 4] rag_validator.py validate_with_rag alias")
try:
    from app.services.rag_validator import check_layer2, validate_with_rag
    ok("check_layer2 + validate_with_rag imported OK")
    # Smoke test with no reference chunks (should return full marks)
    result = validate_with_rag("Some generated content about photosynthesis.", [])
    assert "layer2_total" in result, f"Missing layer2_total key: {result.keys()}"
    assert "layer2_passed" in result, f"Missing layer2_passed key: {result.keys()}"
    assert "grounding_score" in result, f"Missing grounding_score key: {result.keys()}"
    assert result["layer2_passed"] is True, "No-ref case should be PASS"
    ok("validate_with_rag(no refs) → layer2_passed=True, spec keys present")
except ImportError as e:
    fail("FIX 4 import failure", str(e))
except Exception as e:
    fail("FIX 4 smoke test failed", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 5 — POST /adaptive/generate endpoint
# ─────────────────────────────────────────────────────────────
print("\n[FIX 5] POST /adaptive/generate endpoint in adaptive_learning.py")
try:
    from app.routers.adaptive_learning import router as adaptive_router
    paths = [r.path for r in adaptive_router.routes]
    methods_by_path = {r.path: list(r.methods) for r in adaptive_router.routes}
    assert "/adaptive/generate" in paths, f"Missing endpoint. Routes: {paths}"
    assert "POST" in methods_by_path.get("/adaptive/generate", []), \
        "Endpoint exists but is not POST"
    ok("POST /adaptive/generate endpoint confirmed in router")
except ImportError as e:
    fail("FIX 5 import failure", str(e))
except AssertionError as e:
    fail("FIX 5 endpoint missing", str(e))
except Exception as e:
    fail("FIX 5 unexpected error", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 6 — database.py RAG collections
# ─────────────────────────────────────────────────────────────
print("\n[FIX 6] database.py RAG/pipeline collections present")
try:
    from app.db.database import (
        config_collection,
        reference_uploads_collection,
        validation_results_collection,
        benchmark_results_collection,
        ping_db,
        ensure_indexes,
    )
    ok("config_collection imported OK")
    ok("reference_uploads_collection imported OK")
    ok("validation_results_collection imported OK")
    ok("benchmark_results_collection imported OK")
    ok("ping_db + ensure_indexes imported OK")
except ImportError as e:
    fail("FIX 6 import failure", str(e))
except Exception as e:
    fail("FIX 6 unexpected error", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 7 — reference_upload.py router endpoints
# ─────────────────────────────────────────────────────────────
print("\n[FIX 7] reference_upload.py router (4 teacher endpoints)")
try:
    from app.routers.reference_upload import router as ref_router
    paths = [r.path for r in ref_router.routes]
    required = [
        "/reference/upload",
        "/reference/uploads",
        "/reference/generate-description",
    ]
    for ep in required:
        assert ep in paths, f"Missing endpoint: {ep}. Found: {paths}"
    ok(f"All required teacher endpoints present: {required}")
except ImportError as e:
    fail("FIX 7 import failure", str(e))
except AssertionError as e:
    fail("FIX 7 endpoint missing", str(e))
except Exception as e:
    fail("FIX 7 unexpected error", str(e))

# ─────────────────────────────────────────────────────────────
# FIX 8 — model_manager.py router (5 Super Admin endpoints)
# ─────────────────────────────────────────────────────────────
print("\n[FIX 8] model_manager.py router (5+ Super Admin endpoints)")
try:
    from app.routers.model_manager import router as mm_router
    paths = [r.path for r in mm_router.routes]
    required = [
        "/admin/models/leaderboard",
        "/admin/models/benchmark",
        "/admin/models/set-active",
        "/admin/models/health",
    ]
    for ep in required:
        assert ep in paths, f"Missing endpoint: {ep}. Found: {paths}"
    ok(f"All required Super Admin endpoints present: {required}")
except ImportError as e:
    fail("FIX 8 import failure", str(e))
except AssertionError as e:
    fail("FIX 8 endpoint missing", str(e))
except Exception as e:
    fail("FIX 8 unexpected error", str(e))

# ─────────────────────────────────────────────────────────────
# BONUS — Ollama connectivity check
# ─────────────────────────────────────────────────────────────
print("\n[BONUS] Ollama connectivity + 3 worker models")
async def check_ollama():
    try:
        from app.services.ollama_service import check_ollama_health, WORKER_MODELS
        health = await check_ollama_health()
        status = health.get("status", "unknown")
        available = health.get("available_models", [])
        if status == "online":
            ok(f"Ollama is ONLINE at http://localhost:11434")
        else:
            fail("Ollama is OFFLINE", health.get("error", ""))
        required_models = list(WORKER_MODELS.keys())
        for model in required_models:
            matched = any(model in m for m in available)
            if matched:
                ok(f"Model '{model}' is available in Ollama")
            else:
                fail(f"Model '{model}' NOT found in Ollama", f"Available: {available}")
    except Exception as e:
        fail("Ollama health check failed", str(e))

asyncio.run(check_ollama())

# ─────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  FINAL CONFIRMATION CHECKLIST")
print("=" * 60)
passed = [r for r in results if r[0]]
failed = [r for r in results if not r[0]]

for _, msg in results:
    icon = PASS if _ else FAIL
    print(f"  {icon}  {msg}")

print()
print(f"  Total checks : {len(results)}")
print(f"  Passed       : {len(passed)}")
print(f"  Failed       : {len(failed)}")

if not failed:
    print()
    print("  *** ALL 8 FIXES VERIFIED — SYSTEM READY FOR TESTING ***")
    sys.exit(0)
else:
    print()
    print(f"  !!! {len(failed)} check(s) failed — review above !!!")
    sys.exit(1)
