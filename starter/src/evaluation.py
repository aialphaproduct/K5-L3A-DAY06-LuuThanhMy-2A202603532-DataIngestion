"""Chấm tự động Hit@1 / Evidence Hit@3 và giữ trace từng câu. Không suy ra nhãn thủ công từ đây."""
from __future__ import annotations

import re
from typing import Any


def squash(text: str) -> str:
    """Chuẩn hóa khoảng trắng để so khớp evidence không phụ thuộc cách xuống dòng."""
    return re.sub(r"\s+", " ", text).strip()


def gold_alternatives(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Gold chính + các nguồn/trang hợp lệ khác (`alt_gold`); khớp một trong số đó là đủ."""
    primary = {"gold_source_id": question["gold_source_id"], "gold_page": question["gold_page"],
               "evidence_quote": question["evidence_quote"]}
    return [primary, *question.get("alt_gold", [])]


def _check_hit(hit: dict[str, Any], golds: list[dict[str, Any]]) -> dict[str, bool]:
    metadata = hit["metadata"]
    pages = {int(n) for n in str(metadata.get("pages", metadata["page"])).split(",")}
    text = squash(hit["text"])
    source_page_match = evidence_full = is_hit = False
    for gold in golds:
        same_place = metadata["document_id"] == gold["gold_source_id"] and int(gold["gold_page"]) in pages
        has_evidence = squash(gold["evidence_quote"]) in text
        source_page_match |= same_place
        evidence_full |= has_evidence
        is_hit |= same_place and has_evidence
    return {"source_page_match": source_page_match, "evidence_full": evidence_full, "is_hit": is_hit}


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Một hit = chunk thuộc đúng nguồn + trang gold VÀ chứa đủ nguyên văn evidence."""
    golds = gold_alternatives(question)
    judged = [{**hit, **_check_hit(hit, golds)} for hit in hits]
    hit_ranks = [hit["rank"] for hit in judged if hit["is_hit"]]
    gold_sources = {gold["gold_source_id"] for gold in golds}
    if hit_ranks:
        failure = None
    elif any(hit["source_page_match"] for hit in judged):
        failure = "dung_trang_thieu_evidence"  # đúng trang gold nhưng evidence không nằm trọn trong chunk
    elif any(hit["metadata"]["document_id"] in gold_sources for hit in judged):
        failure = "dung_tai_lieu_sai_trang"
    else:
        failure = "sai_tai_lieu"
    return {
        "question_id": question["id"], "split": question["split"], "type": question.get("type"),
        "question": question["question"], "expected_answer": question["expected_answer"],
        "gold": golds, "filter_requested": question.get("filter"),
        "hit_at_1": 1 in hit_ranks, "hit_at_3": bool(hit_ranks),
        "first_hit_rank": min(hit_ranks, default=None), "failure_type": failure, "top_k": judged,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Tổng hợp theo (strategy, split): luôn kèm tử số/mẫu số, tách riêng nhóm câu có filter."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        groups.setdefault((trace["strategy"], trace["split"]), []).append(trace)
    rows = []
    for (strategy, split), items in sorted(groups.items()):
        total = len(items)
        with_filter = [t for t in items if t["filter_requested"]]
        compliant = [t for t in with_filter if all(
            hit["metadata"].get(key) == value
            for hit in t["top_k"] for key, value in t["filter_requested"].items())]
        failures: dict[str, int] = {}
        for trace in items:
            if trace["failure_type"]:
                failures[trace["failure_type"]] = failures.get(trace["failure_type"], 0) + 1
        rows.append({
            "strategy": strategy, "split": split, "n_questions": total,
            "hit_at_1": sum(t["hit_at_1"] for t in items), "hit_at_3": sum(t["hit_at_3"] for t in items),
            "hit_at_1_rate": round(sum(t["hit_at_1"] for t in items) / total, 4),
            "hit_at_3_rate": round(sum(t["hit_at_3"] for t in items) / total, 4),
            "filter_questions": len(with_filter),
            "filter_questions_hit_at_3": sum(t["hit_at_3"] for t in with_filter) if with_filter else None,
            "filter_questions_top3_in_scope": len(compliant) if with_filter else None,
            "failure_types": failures,
            "missed_question_ids": [t["question_id"] for t in items if not t["hit_at_3"]],
            "mean_query_ms": round(sum(t["query_ms"] for t in items) / total, 2),
        })
    return {"rows": rows}
