"""
layer1_validation.py — Layer 1: Simple Content Quality Checks

Scoring (40 pts total):
    ROUGE score     → relevance to topic                    (15 pts, threshold >= 0.4)
    BERTScore       → semantic meaning match                (10 pts, threshold >= 0.85)
    Word count      → lesson>=300, mcq>=100, tutor>=50      (8 pts)
    Structure check → headings, summary, key_concepts       (7 pts)

Pass threshold: >= 25/40
If fails → regenerate once → if fails again → flag Super Admin
"""

from __future__ import annotations

import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)

TaskType = Literal["lesson", "mcq", "tutor"]

# Thresholds
ROUGE_THRESHOLD = 0.4
BERT_THRESHOLD  = 0.85
WORD_COUNTS     = {"lesson": 300, "mcq": 100, "tutor": 50}

# Point allocations
ROUGE_MAX      = 15
BERT_MAX       = 10
WORDCOUNT_MAX  = 8
STRUCTURE_MAX  = 7
LAYER1_TOTAL   = ROUGE_MAX + BERT_MAX + WORDCOUNT_MAX + STRUCTURE_MAX  # 40
LAYER1_PASS    = 25


def check_layer1(
    generated_content: str,
    topic: str,
    task_type: TaskType = "lesson",
) -> dict:
    """
    Run all Layer 1 checks and return a scored result dict.

    Args:
        generated_content: The AI-generated text (lesson content, quiz JSON string, tutor reply).
        topic:             The lesson/quiz topic used to compute relevance.
        task_type:         "lesson", "mcq", or "tutor".

    Returns:
        {
            rouge_score, rouge_pts,
            bert_score,  bert_pts,
            word_count,  wordcount_pts,
            structure_pts,
            layer1_total,
            passed,
            details: { ... }
        }
    """
    result: dict = {}

    # ── ROUGE Score ────────────────────────────────────────────────────────────
    rouge_score, rouge_pts = _compute_rouge(generated_content, topic)
    result["rouge_score"] = round(rouge_score, 4)
    result["rouge_pts"]   = rouge_pts

    # ── BERTScore ─────────────────────────────────────────────────────────────
    bert_score, bert_pts = _compute_bert(generated_content, topic)
    result["bert_score"] = round(bert_score, 4)
    result["bert_pts"]   = bert_pts

    # ── Word Count ────────────────────────────────────────────────────────────
    word_count, wc_pts = _compute_wordcount(generated_content, task_type)
    result["word_count"]    = word_count
    result["wordcount_pts"] = wc_pts

    # ── Structure Check ───────────────────────────────────────────────────────
    struct_pts, struct_details = _compute_structure(generated_content, task_type)
    result["structure_pts"]    = struct_pts
    result["structure_details"] = struct_details

    # ── Final ─────────────────────────────────────────────────────────────────
    total = rouge_pts + bert_pts + wc_pts + struct_pts
    result["layer1_total"] = total
    result["passed"]       = total >= LAYER1_PASS

    logger.info(
        "Layer1 [%s]: ROUGE=%.2f(%dpts) BERT=%.2f(%dpts) WC=%d(%dpts) STR=%dpts → TOTAL=%d/%d %s",
        task_type, rouge_score, rouge_pts, bert_score, bert_pts,
        word_count, wc_pts, struct_pts, total, LAYER1_TOTAL,
        "PASS" if result["passed"] else "FAIL",
    )
    return result


# ── ROUGE ──────────────────────────────────────────────────────────────────────

def _compute_rouge(content: str, topic: str) -> tuple[float, int]:
    """Compute ROUGE-L recall between content and topic as reference."""
    try:
        from rouge_score import rouge_scorer
        scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
        scores = scorer.score(topic, content)
        r = scores["rougeL"].recall
        pts = ROUGE_MAX if r >= ROUGE_THRESHOLD else int(ROUGE_MAX * (r / ROUGE_THRESHOLD))
        return r, pts
    except ImportError:
        logger.warning("rouge_score not installed — skipping ROUGE check, awarding 0 pts")
        return 0.0, 0
    except Exception as e:
        logger.warning("ROUGE computation failed: %s", e)
        return 0.0, 0


# ── BERTScore ─────────────────────────────────────────────────────────────────

def _compute_bert(content: str, topic: str) -> tuple[float, int]:
    """Compute BERTScore F1 between content and topic."""
    try:
        from bert_score import score as bert_score_fn
        # Use first 512 words to keep it fast
        content_short = " ".join(content.split()[:512])
        P, R, F1 = bert_score_fn(
            [content_short], [topic],
            lang="en",
            model_type="distilbert-base-uncased",
            verbose=False,
        )
        f1 = float(F1[0])
        pts = BERT_MAX if f1 >= BERT_THRESHOLD else int(BERT_MAX * (f1 / BERT_THRESHOLD))
        return f1, pts
    except ImportError:
        logger.warning("bert_score not installed — skipping BERTScore, awarding 0 pts")
        return 0.0, 0
    except Exception as e:
        logger.warning("BERTScore computation failed: %s", e)
        return 0.0, 0


# ── Word Count ─────────────────────────────────────────────────────────────────

def _compute_wordcount(content: str, task_type: TaskType) -> tuple[int, int]:
    """Check if content meets minimum word count for the task type."""
    words = len(content.split())
    threshold = WORD_COUNTS.get(task_type, 100)
    if words >= threshold:
        pts = WORDCOUNT_MAX
    elif words >= threshold * 0.7:
        pts = int(WORDCOUNT_MAX * 0.6)
    else:
        pts = 0
    return words, pts


# ── Structure ─────────────────────────────────────────────────────────────────

def _compute_structure(content: str, task_type: TaskType) -> tuple[int, dict]:
    """
    Check structural elements based on task type.
    Lesson: headings (##), summary present, key_concepts present.
    MCQ:    questions array, options, correct_answer.
    Tutor:  non-empty response, no error messages.
    """
    details: dict = {}
    pts = 0

    if task_type == "lesson":
        has_heading = bool(re.search(r'^#+\s+\w+', content, re.MULTILINE))
        has_summary = "summary" in content.lower() or len(content) > 500
        has_concepts = "concept" in content.lower() or "key" in content.lower()

        details["has_heading"]  = has_heading
        details["has_summary"]  = has_summary
        details["has_concepts"] = has_concepts

        if has_heading:  pts += 3
        if has_summary:  pts += 2
        if has_concepts: pts += 2

    elif task_type == "mcq":
        has_questions = "question" in content.lower() or "?" in content
        has_options   = "option" in content.lower() or '"A"' in content or "A)" in content
        has_answer    = "correct" in content.lower() or "answer" in content.lower()

        details["has_questions"] = has_questions
        details["has_options"]   = has_options
        details["has_answer"]    = has_answer

        if has_questions: pts += 3
        if has_options:   pts += 2
        if has_answer:    pts += 2

    elif task_type == "tutor":
        is_nonempty    = len(content.strip()) > 20
        no_error_msg   = "error" not in content.lower()[:100]
        is_encouraging = any(w in content.lower() for w in ["great", "good", "let me", "sure", "happy to"])

        details["is_nonempty"]    = is_nonempty
        details["no_error_msg"]   = no_error_msg
        details["is_encouraging"] = is_encouraging

        if is_nonempty:    pts += 4
        if no_error_msg:   pts += 2
        if is_encouraging: pts += 1

    pts = min(pts, STRUCTURE_MAX)
    return pts, details


# ── Spec-compatible public API ─────────────────────────────────────────────────
# The audit spec defines 6 specific public functions.  Below are thin wrappers
# over the private helpers so both the spec interface and existing callers work.

def check_rouge_relevance(prompt: str, ai_response: str) -> float:
    """
    Spec-compatible ROUGE relevance check.
    Returns float 0.0 – 1.0 (average of rouge1 and rougeL fmeasure).
    """
    try:
        from rouge_score import rouge_scorer
        scorer = rouge_scorer.RougeScorer(["rouge1", "rougeL"], use_stemmer=True)
        scores = scorer.score(target=prompt, prediction=ai_response)
        avg = (scores["rouge1"].fmeasure + scores["rougeL"].fmeasure) / 2.0
        return round(avg, 4)
    except ImportError:
        logger.warning("rouge_score not installed — returning 0.0")
        return 0.0
    except Exception as e:
        logger.warning("check_rouge_relevance failed: %s", e)
        return 0.0


def check_bert_similarity(prompt: str, ai_response: str) -> float:
    """
    Spec-compatible BERTScore similarity check.
    Returns F1 float 0.0 – 1.0.
    """
    try:
        from bert_score import score as bert_score_fn
        content_short = " ".join(ai_response.split()[:512])
        P, R, F1 = bert_score_fn(
            cands=[content_short],
            refs=[prompt],
            lang="en",
            verbose=False,
        )
        return round(float(F1.mean().item()), 4)
    except ImportError:
        logger.warning("bert_score not installed — returning 0.0")
        return 0.0
    except Exception as e:
        logger.warning("check_bert_similarity failed: %s", e)
        return 0.0


def check_completeness(ai_response: str, task_type: TaskType) -> dict:
    """
    Spec-compatible completeness check.
    Returns {"word_count": int, "passed": bool}
    """
    word_count = len(ai_response.split())
    minimums: dict[str, int] = {"lesson": 300, "mcq": 100, "tutor": 50}
    threshold = minimums.get(task_type, 100)
    return {"word_count": word_count, "passed": word_count >= threshold}


def check_lesson_structure(lesson: str) -> dict:
    """
    Spec-compatible lesson structure check.
    Returns dict with individual boolean checks and overall "passed" key.
    """
    import re as _re
    word_count = len(lesson.split())
    checks = {
        "has_heading":        bool(_re.search(r"^##\s+\w+", lesson, _re.MULTILINE)),
        "has_summary":        "summary" in lesson.lower(),
        "has_key_concept":    "key concept" in lesson.lower() or "key_concept" in lesson.lower(),
        "word_count_ok":      word_count >= 300,
        "has_difficulty_word": any(w in lesson.lower() for w in ["beginner", "intermediate", "advanced"]),
    }
    checks["passed"] = all(checks.values())
    checks["word_count"] = word_count
    return checks


def check_mcq_structure(mcq_json: str) -> dict:
    """
    Spec-compatible MCQ structure check.
    Returns dict with individual boolean checks and overall "passed" key.
    """
    import json as _json
    checks = {
        "all_required_fields": False,
        "exactly_4_options":   False,
        "explanation_ok":      False,
        "correct_answer_valid": False,
    }
    try:
        data = _json.loads(mcq_json) if isinstance(mcq_json, str) else mcq_json
        questions = data.get("questions", [data]) if isinstance(data, dict) else data
        if not questions:
            checks["passed"] = False
            return checks

        q = questions[0] if isinstance(questions, list) else questions
        req_fields = {"question", "options", "correct_answer", "explanation"}
        checks["all_required_fields"] = req_fields.issubset(set(q.keys()))
        checks["exactly_4_options"]   = isinstance(q.get("options"), list) and len(q["options"]) == 4
        checks["explanation_ok"]      = len(str(q.get("explanation", ""))) > 30
        checks["correct_answer_valid"] = q.get("correct_answer") in ("A", "B", "C", "D")
    except Exception:
        pass

    checks["passed"] = all(checks.values())
    return checks


def calculate_layer1_score(
    rouge: float,
    bert: float,
    completeness: dict,
    structure: dict,
) -> dict:
    """
    Spec-compatible scoring formula (40 pts total).
    rouge_points     = min(rouge * 15, 15)
    bert_points      = min((bert - 0.7) * 50, 10)  ← spec formula
    complete_points  = 8 if completeness["passed"] else 0
    structure_points = 7 if structure["passed"] else 0
    layer1_passed    = total >= 25
    """
    rouge_pts    = round(min(rouge * 15, 15), 2)
    bert_pts     = round(min(max((bert - 0.7) * 50, 0), 10), 2)
    complete_pts = 8 if completeness.get("passed") else 0
    struct_pts   = 7 if structure.get("passed") else 0
    total        = rouge_pts + bert_pts + complete_pts + struct_pts

    return {
        "rouge_points":     rouge_pts,
        "bert_points":      bert_pts,
        "complete_points":  complete_pts,
        "structure_points": struct_pts,
        "layer1_total":     round(total, 2),
        "layer1_passed":    total >= LAYER1_PASS,
    }

