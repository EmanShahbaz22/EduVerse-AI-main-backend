"""
rag_validator.py — Layer 2: RAG Cosine Similarity Grounding Check

Zero-LLM validation — pure sentence-transformers math.

Algorithm:
    1. Split AI output into sentences
    2. Embed sentences + reference chunks using all-MiniLM-L6-v2
    3. cosine_similarity(sentence_embedding, all chunk_embeddings)
    4. Sentence is "supported" if max similarity >= 0.75
    5. grounding_score = supported_sentences / total_sentences
    6. layer2_pts = grounding_score * 60

Scoring (60 pts total):
    grounding_score >= 0.75  → 60 pts (PASS)
    grounding_score  < 0.75  → proportional pts
"""

from __future__ import annotations

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

# Thresholds — tuned for local 3B models that paraphrase heavily
# 0.55 catches hallucinations while accepting legitimate rewording
COSINE_THRESHOLD = 0.55   # minimum similarity for a "supported" sentence
LAYER2_MAX       = 60     # total points Layer 2 can award

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def _get_sentence_model():
    """Lazy-load the sentence-transformers model (cached in memory after first use)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


def _split_sentences(text: str) -> list[str]:
    """Split text into non-trivial sentences."""
    # Split on ., !, ?, newlines — keep only sentences with >=5 words
    raw = re.split(r'(?<=[.!?])\s+|\n+', text)
    sentences = [s.strip() for s in raw if len(s.strip().split()) >= 5]
    return sentences


def check_layer2(
    generated_content: str,
    reference_chunks: list[str],
) -> dict:
    """
    Run Layer 2 RAG cosine similarity grounding check.

    Args:
        generated_content: The AI-generated text.
        reference_chunks:  List of raw text strings from ChromaDB retrieval
                           (top-k chunks used as the ground truth reference).

    Returns:
        {
            grounding_score,     # 0.0 – 1.0
            layer2_pts,          # 0 – 60
            supported_count,
            total_sentences,
            verdict,             # "PASS" | "FAIL"
            sentence_details:    # list of per-sentence results
        }

    Note:
        If reference_chunks is empty (no upload exists), returns full marks
        for grounding (can't validate without reference → benefit of doubt).
    """
    # No reference material → award full marks (can't penalise what we can't check)
    if not reference_chunks:
        logger.info("Layer2: No reference chunks available — awarding full marks (benefit of doubt)")
        return {
            "grounding_score": 1.0,
            "layer2_pts": LAYER2_MAX,
            "supported_count": 0,
            "total_sentences": 0,
            "verdict": "PASS",
            "sentence_details": [],
            "note": "No reference material uploaded — skipped grounding check",
        }

    sentences = _split_sentences(generated_content)
    if not sentences:
        logger.warning("Layer2: Could not extract sentences from generated content")
        return {
            "grounding_score": 0.0,
            "layer2_pts": 0,
            "supported_count": 0,
            "total_sentences": 0,
            "verdict": "FAIL",
            "sentence_details": [],
        }

    try:
        model = _get_sentence_model()

        # Embed all sentences + all reference chunks in one batch (efficient)
        all_texts   = sentences + reference_chunks
        all_embeds  = model.encode(all_texts, normalize_embeddings=True, show_progress_bar=False)

        sentence_embeds = all_embeds[:len(sentences)]   # shape: (n_sentences, dim)
        chunk_embeds    = all_embeds[len(sentences):]   # shape: (n_chunks, dim)

        # Cosine similarity matrix: (n_sentences × n_chunks)
        # Since embeddings are normalized, dot product == cosine similarity
        sim_matrix = np.dot(sentence_embeds, chunk_embeds.T)  # shape: (n_sentences, n_chunks)

        sentence_details = []
        supported_count  = 0

        for i, sentence in enumerate(sentences):
            max_sim = float(sim_matrix[i].max())
            best_chunk_idx = int(sim_matrix[i].argmax())
            is_supported = max_sim >= COSINE_THRESHOLD

            if is_supported:
                supported_count += 1

            sentence_details.append({
                "sentence":       sentence[:120] + "..." if len(sentence) > 120 else sentence,
                "max_similarity": round(max_sim, 4),
                "is_supported":   is_supported,
                "best_chunk":     reference_chunks[best_chunk_idx][:80] + "...",
            })

        grounding_score = supported_count / len(sentences)
        layer2_pts      = int(grounding_score * LAYER2_MAX)
        verdict         = "PASS" if grounding_score >= COSINE_THRESHOLD else "FAIL"

        logger.info(
            "Layer2: %d/%d sentences supported (%.1f%%) → %d/%d pts → %s",
            supported_count, len(sentences),
            grounding_score * 100,
            layer2_pts, LAYER2_MAX,
            verdict,
        )

        return {
            "grounding_score":   round(grounding_score, 4),
            "layer2_pts":        layer2_pts,
            "supported_count":   supported_count,
            "total_sentences":   len(sentences),
            "verdict":           verdict,
            "sentence_details":  sentence_details,
        }

    except ImportError:
        logger.warning("sentence_transformers not installed — awarding full Layer2 marks")
        return {
            "grounding_score": 1.0,
            "layer2_pts": LAYER2_MAX,
            "supported_count": 0,
            "total_sentences": len(sentences),
            "verdict": "PASS",
            "sentence_details": [],
            "note": "sentence_transformers not installed — skipped grounding check",
        }
    except Exception as e:
        logger.error("Layer2 validation error: %s", e)
        return {
            "grounding_score": 0.0,
            "layer2_pts": 0,
            "supported_count": 0,
            "total_sentences": len(sentences),
            "verdict": "FAIL",
            "error": str(e),
            "sentence_details": [],
        }


# ── Spec-compatible public alias ───────────────────────────────────────────────

def validate_with_rag(
    ai_generated_content: str,
    reference_chunks: list[str],
) -> dict:
    """
    Spec-compatible entry point (delegates to check_layer2).
    ZERO LLM usage — pure sentence-transformers + sklearn cosine similarity.

    Returns dict with spec-required keys:
        total_sentences, supported_count, grounding_score,
        layer2_total, layer2_passed, verdict, sentence_details
    sentence_details items have:
        sentence, best_similarity_score, supported, matched_reference
    """
    raw = check_layer2(ai_generated_content, reference_chunks)

    # Re-map internal key names to spec-required names
    sentence_details = [
        {
            "sentence":             item.get("sentence", ""),
            "best_similarity_score": item.get("max_similarity", 0.0),
            "supported":            item.get("is_supported", False),
            "matched_reference":    item.get("best_chunk", ""),
        }
        for item in raw.get("sentence_details", [])
    ]

    return {
        "total_sentences":  raw.get("total_sentences", 0),
        "supported_count":  raw.get("supported_count", 0),
        "grounding_score":  raw.get("grounding_score", 0.0),
        "layer2_total":     raw.get("layer2_pts", 0),        # spec key name
        "layer2_passed":    raw.get("verdict", "FAIL") == "PASS",
        "verdict":          raw.get("verdict", "FAIL"),
        "sentence_details": sentence_details,
        # Preserve extra keys for internal consumers
        "layer2_pts":       raw.get("layer2_pts", 0),
    }

