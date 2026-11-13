"""Điểm vào của pipeline Day 06. Chạy từ thư mục `starter/`:

    python -m src.run ingest      # đọc PDF -> chuẩn hóa -> chunk -> embed -> lưu Chroma
    python -m src.run validate    # kiểm chứng evidence của benchmark trên text đã chuẩn hóa
    python -m src.run eval --split dev
    python -m src.run eval --split test --frozen   # chỉ chạy sau khi đã chốt cấu hình trên dev
    python -m src.run audit-sheets                 # sinh bảng chấm thủ công
    python -m src.run audit-summary                # tổng hợp nhãn đã chốt
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from .chunking import by_article, fixed_size
from .evaluation import evaluate_query, gold_alternatives, squash, summarize
from .ingestion import load_pdf
from .models import Chunk, DocumentPage

ROOT = Path(__file__).resolve().parents[2]
STARTER = ROOT / "starter"
DATA = ROOT / "data"
RESULTS = ROOT / "results"
BENCHMARK = ROOT / "benchmark" / "questions.jsonl"
MIN_CONTENT_PAGES = 30


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def load_config() -> dict[str, Any]:
    return read_json(STARTER / "config.json")


def versions() -> dict[str, str]:
    names = ["chromadb", "sentence-transformers", "torch", "transformers", "PyMuPDF", "numpy"]
    found = {"python": platform.python_version(), "platform": platform.platform()}
    for name in names:
        try:
            found[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            found[name] = "not installed"
    return found


# ----------------------------------------------------------------------------- corpus

def load_corpus() -> tuple[list[DocumentPage], list[dict[str, Any]], list[dict[str, Any]]]:
    """Đọc mọi nguồn trong data/sources.json; kiểm tra SHA-256 để chắc đúng phiên bản file."""
    sources = read_json(DATA / "sources.json")["sources"]
    pages: list[DocumentPage] = []
    log: list[dict[str, Any]] = []
    for source in sources:
        path = ROOT / source["local_path"]
        if not path.exists():
            raise FileNotFoundError(f"Thiếu file nguồn {path}; tải lại theo URL trong data/sources.json")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise RuntimeError(f"{path.name}: SHA-256 khác bản đã ghi trong sources.json ({digest})")
        doc_pages, doc_log = load_pdf(path, source["source_id"], source["local_path"],
                                      doc_metadata=source["metadata"],
                                      appendix_from_page=source.get("appendix_from_page"))
        pages.extend(doc_pages)
        log.extend(doc_log)
    return pages, log, sources


def build_manifest(pages: list[DocumentPage], log: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict:
    documents = []
    for source in sources:
        doc_pages = [p for p in pages if p.document_id == source["source_id"]]
        content = [p for p in doc_pages if p.metadata["section"] == "noi_dung"]
        documents.append({
            **{key: source[key] for key in ("source_id", "title", "source_url", "landing_page", "local_path",
                                             "sha256", "license_or_permission", "accessed_at", "version_note")},
            "metadata": source["metadata"],
            "physical_pages": len([e for e in log if e["document_id"] == source["source_id"]]),
            "content_pages_counted": [p.page for p in content],
            "appendix_pages_indexed_not_counted": [p.page for p in doc_pages if p.metadata["section"] == "phu_luc"],
            "pages_excluded": [e for e in log if e["document_id"] == source["source_id"] and e["status"] != "ok"],
            "normalized_chars_per_page": {str(p.page): len(p.text) for p in doc_pages},
            "normalized_chars_total": sum(len(p.text) for p in doc_pages),
        })
    total = sum(len(d["content_pages_counted"]) for d in documents)
    return {
        "generated_at": now(), "generated_by": "python -m src.run ingest",
        "page_id_rule": "page = số trang vật lý của file PDF, bắt đầu từ 1 (không phải số trang Công báo in trên đầu trang).",
        "counting_rule": ("Trang nội dung = trang PDF thuộc phần văn bản chính (section=noi_dung), trích được "
                          ">= 200 ký tự sau chuẩn hóa. Hai file không có trang bìa/mục lục/trang trống. "
                          "Trang phụ lục biểu mẫu (section=phu_luc) vẫn được index nhưng KHÔNG tính vào số trang nội dung."),
        "content_pages_total": total, "minimum_required": MIN_CONTENT_PAGES, "documents": documents,
    }


def build_chunks(pages: list[DocumentPage], index_config: dict[str, Any]) -> list[Chunk]:
    if index_config["chunking"] == "fixed":
        return [chunk for page in pages
                for chunk in fixed_size(page, index_config["size"], index_config["overlap"])]
    if index_config["chunking"] == "structure":
        chunks: list[Chunk] = []
        for document_id in dict.fromkeys(page.document_id for page in pages):
            chunks.extend(by_article([p for p in pages if p.document_id == document_id], index_config["max_size"]))
        return chunks
    raise ValueError(f"Chưa hỗ trợ kiểu chunking {index_config['chunking']}")


def open_backends(config: dict[str, Any]):
    """Khởi tạo model và store thật; mọi lỗi được ném ra, không có đường lui sang mock."""
    from .embeddings import MyEmbedder
    from .store import ChromaStore

    embedder = MyEmbedder(config["embedding_model"])
    store = ChromaStore(str(STARTER / config["chroma_dir"]), embedder.dimensions, embedder.model_name)
    return embedder, store


def cmd_ingest(_: argparse.Namespace) -> None:
    config = load_config()
    started = time.perf_counter()
    pages, log, sources = load_corpus()
    read_seconds = time.perf_counter() - started
    manifest = build_manifest(pages, log, sources)
    if manifest["content_pages_total"] < MIN_CONTENT_PAGES:
        raise RuntimeError(f"Corpus chỉ có {manifest['content_pages_total']} trang nội dung (< {MIN_CONTENT_PAGES})")
    write_json(DATA / "manifest.json", manifest)
    write_jsonl(DATA / "processed" / "pages.jsonl", [
        {"document_id": p.document_id, "source": p.source, "page": p.page, "chars": len(p.text),
         "metadata": p.metadata, "text": p.text} for p in pages])
    write_json(RESULTS / "ingestion_log.json", {
        "generated_at": now(), "read_seconds": round(read_seconds, 3),
        "pages_ok": sum(e["status"] == "ok" for e in log),
        "pages_not_ok": [e for e in log if e["status"] != "ok"], "pages": log})
    print(f"Đọc {len(log)} trang vật lý, {len(pages)} trang dùng được, "
          f"{manifest['content_pages_total']} trang nội dung được tính ({read_seconds:.2f}s)")

    embedder, store = open_backends(config)
    stats = {"generated_at": now(), "embedding_model": embedder.model_name, "dimensions": embedder.dimensions,
             "max_tokens": embedder.max_tokens, "device": "cpu", "document_prefix": embedder.document_prefix,
             "query_prefix": embedder.query_prefix, "vector_store": "chromadb PersistentClient",
             "distance": "cosine (hnsw:space=cosine, tìm kiếm xấp xỉ HNSW mặc định)",
             "versions": versions(), "cost_usd": 0.0, "indexes": {}}
    for name, index_config in config["indexes"].items():
        t0 = time.perf_counter()
        chunks = build_chunks(pages, index_config)
        t1 = time.perf_counter()
        tokens = embedder.count_tokens([c.text for c in chunks])
        t2 = time.perf_counter()
        vectors = embedder.embed_documents([c.text for c in chunks])
        t3 = time.perf_counter()
        upsert = store.upsert(name, chunks, vectors)
        t4 = time.perf_counter()
        lengths = sorted(len(c.text) for c in chunks)
        stats["indexes"][name] = {
            "config": index_config, "collection": f"day06_{name}", "chunk_count": len(chunks),
            "chars_total": sum(lengths), "tokens_total": sum(tokens),
            "chunk_chars": {"min": lengths[0], "p50": int(statistics.median(lengths)),
                            "mean": round(statistics.mean(lengths), 1),
                            "p95": lengths[int(0.95 * (len(lengths) - 1))], "max": lengths[-1]},
            "chunks_over_token_limit": sum(t > embedder.max_tokens for t in tokens),
            "seconds": {"chunk": round(t1 - t0, 3), "embed": round(t3 - t2, 3), "upsert": round(t4 - t3, 3),
                        "total": round((t1 - t0) + (t3 - t2) + (t4 - t3), 3)},
            "upsert": upsert,
        }
        write_jsonl(RESULTS / f"chunks_{name}.jsonl", [
            {"chunk_id": c.chunk_id, "document_id": c.document_id, "source": c.source, "page": c.page,
             "metadata": c.metadata, "tokens": t, "text": c.text} for c, t in zip(chunks, tokens)])
        with (RESULTS / "reingest_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": now(), "index": name, **upsert}, ensure_ascii=False) + "\n")
        print(f"[{name}] {len(chunks)} chunk | embed {t3 - t2:.1f}s | upsert {upsert}")
    write_json(RESULTS / "ingest_stats.json", stats)


# ----------------------------------------------------------------------------- benchmark

def load_questions() -> list[dict[str, Any]]:
    questions = read_jsonl(BENCHMARK)
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("ID câu hỏi bị trùng")
    return questions


def cmd_validate(_: argparse.Namespace) -> None:
    """Evidence phải là nguyên văn nằm trọn trong MỘT trang gold sau chuẩn hóa."""
    pages = {(p["document_id"], p["page"]): squash(p["text"]) for p in read_jsonl(DATA / "processed" / "pages.jsonl")}
    filter_values: dict[str, set] = {}
    for page in read_jsonl(DATA / "processed" / "pages.jsonl"):
        for key, value in page["metadata"].items():
            filter_values.setdefault(key, set()).add(value)
    questions = load_questions()
    report, problems = [], []
    for question in questions:
        checks = []
        for gold in gold_alternatives(question):
            text = pages.get((gold["gold_source_id"], gold["gold_page"]))
            checks.append({**gold, "page_exists": text is not None,
                           "evidence_found": bool(text) and squash(gold["evidence_quote"]) in text})
        filter_ok = all(value in filter_values.get(key, set()) for key, value in (question.get("filter") or {}).items())
        leak = any(squash(g["evidence_quote"]).lower() in question["question"].lower() for g in gold_alternatives(question))
        ok = all(c["page_exists"] and c["evidence_found"] for c in checks) and filter_ok and not leak
        report.append({"id": question["id"], "split": question["split"], "ok": ok, "filter_value_exists": filter_ok,
                       "evidence_copied_into_question": leak, "gold_checks": checks})
        if not ok:
            problems.append(question["id"])
    splits = {s: sum(q["split"] == s for q in questions) for s in ("dev", "test")}
    filters = {s: sum(q["split"] == s and bool(q.get("filter")) for q in questions) for s in ("dev", "test")}
    summary = {"generated_at": now(), "questions": len(questions), "split_counts": splits,
               "filter_questions": filters, "all_evidence_verified": not problems, "problems": problems}
    write_json(RESULTS / "benchmark_validation.json", {"summary": summary, "questions": report})
    print(json.dumps(summary, ensure_ascii=False))
    if problems or len(questions) < 20 or splits["dev"] < 15 or splits["test"] < 5 \
            or sum(filters.values()) < 3 or filters["test"] < 1:
        sys.exit("Benchmark CHƯA đạt yêu cầu; xem results/benchmark_validation.json")


def cmd_eval(args: argparse.Namespace) -> None:
    config = load_config()
    if args.split == "test" and not args.frozen:
        sys.exit("Chỉ chạy test sau khi đã chốt cấu hình trên dev (benchmark/SELECTION.md); thêm --frozen để xác nhận.")
    questions = [q for q in load_questions() if q["split"] == args.split]
    embedder, store = open_backends(config)
    traces = []
    for question in questions:
        t0 = time.perf_counter()
        vector = embedder.embed_query(question["question"])  # chỉ câu hỏi; không có đáp án hay nguồn gold
        embed_ms = (time.perf_counter() - t0) * 1000
        for name, strategy in config["strategies"].items():
            filters = question.get("filter") if strategy["use_filter"] else None
            t1 = time.perf_counter()
            hits = store.search(strategy["index"], vector, top_k=config["top_k"], filters=filters)
            search_ms = (time.perf_counter() - t1) * 1000
            trace = evaluate_query(question, hits)
            trace.update(strategy=name, index=strategy["index"], filter_applied=filters,
                         query_ms=round(embed_ms + search_ms, 2), search_ms=round(search_ms, 2))
            traces.append(trace)
    write_jsonl(RESULTS / f"trace_{args.split}.jsonl", traces)
    summary = {"generated_at": now(), "split": args.split, "top_k": config["top_k"],
               "embedding_model": embedder.model_name, **summarize(traces)}
    write_json(RESULTS / f"metrics_{args.split}.json", summary)
    for row in summary["rows"]:
        print(f"{row['strategy']} {row['split']}: Hit@1 {row['hit_at_1']}/{row['n_questions']} | "
              f"Hit@3 {row['hit_at_3']}/{row['n_questions']} | trượt: {row['missed_question_ids']}")


# ----------------------------------------------------------------------------- manual audit

AUDIT = RESULTS / "audit"
LABEL_COLUMNS = ["de_xuat_ai", "ly_do_ai", "nhan_chot", "ly_do_chot"]


def coherence_positions(pages: list[dict[str, Any]], count: int = 10) -> list[dict[str, Any]]:
    """Quy tắc cố định trước khi xem điểm: sắp trang nội dung theo (tài liệu, trang), lấy trang thứ 3, 8, 13...

    Vị trí = từ đầu tiên nằm ngay sau điểm giữa của text trang; neo bằng 30 ký tự kể từ đó.
    """
    content = [p for p in pages if p["metadata"]["section"] == "noi_dung"]
    step = len(content) // count
    positions = []
    for i in range(count):
        page = content[2 + i * step]
        text = squash(page["text"])
        start = text.find(" ", len(text) // 2) + 1
        positions.append({"position_id": f"pos{i + 1:02d}", "document_id": page["document_id"],
                          "page": page["page"], "anchor": text[start:start + 30]})
    return positions


def find_chunk(chunks: list[dict[str, Any]], position: dict[str, Any]) -> dict[str, Any] | None:
    for length in (30, 20, 12):
        anchor = position["anchor"][:length]
        for chunk in chunks:
            pages = {int(n) for n in str(chunk["metadata"]["pages"]).split(",")}
            if chunk["document_id"] == position["document_id"] and position["page"] in pages \
                    and anchor in squash(chunk["text"]):
                return chunk
    return None


def write_sheet(path: Path, rows: list[dict[str, Any]], key_columns: list[str]) -> None:
    """Ghi bảng chấm; giữ lại nhãn đã điền ở lần chạy trước nếu khóa dòng không đổi."""
    previous: dict[tuple, dict[str, str]] = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                previous[tuple(row[k] for k in key_columns)] = {c: row.get(c, "") for c in LABEL_COLUMNS}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) + LABEL_COLUMNS)
        writer.writeheader()
        for row in rows:
            kept = previous.get(tuple(str(row[k]) for k in key_columns), dict.fromkeys(LABEL_COLUMNS, ""))
            writer.writerow({**row, **kept})


def cmd_audit_sheets(_: argparse.Namespace) -> None:
    config = load_config()
    pages = read_jsonl(DATA / "processed" / "pages.jsonl")
    page_text = {(p["document_id"], p["page"]): squash(p["text"]) for p in pages}
    sources = {s["source_id"]: s for s in read_json(DATA / "sources.json")["sources"]}
    test = read_jsonl(RESULTS / "trace_test.jsonl")

    # 1) Coherence: cùng 10 vị trí cho mỗi chỉ mục chunk (B và C dùng chung chỉ mục nên chung nhãn).
    rows = []
    for position in coherence_positions(pages):
        for name in config["indexes"]:
            chunk = find_chunk(read_jsonl(RESULTS / f"chunks_{name}.jsonl"), position)
            users = "/".join(s for s, c in config["strategies"].items() if c["index"] == name)
            rows.append({**position, "index": name, "strategies": users,
                         "chunk_id": chunk["chunk_id"] if chunk else "KHÔNG TÌM THẤY",
                         "chunk_chars": len(chunk["text"]) if chunk else 0,
                         "chunk_text": chunk["text"] if chunk else ""})
    write_sheet(AUDIT / "coherence_labels.csv", rows, ["position_id", "index"])

    # 2) Metadata: mọi bản ghi top-3 của các câu test, kèm phép kiểm tự động đối chiếu với nguồn.
    rows = []
    for trace in test:
        for hit in trace["top_k"]:
            meta = hit["metadata"]
            stated = [int(n) for n in str(meta["pages"]).split(",")]
            body = [squash(line) for line in hit["text"].split("\n") if not line.startswith("[") and " – " not in line[:40]]
            on_pages = " ".join(page_text.get((meta["document_id"], n), "") for n in stated)
            source_meta = sources[meta["document_id"]]["metadata"]
            rows.append({
                "question_id": trace["question_id"], "strategy": trace["strategy"], "rank": hit["rank"],
                "chunk_id": hit["chunk_id"], "document_id": meta["document_id"], "source": meta["source"],
                "pages": meta["pages"], "document_type": meta.get("document_type"), "year": meta.get("year"),
                "section": meta.get("section"), "chapter": meta.get("chapter"), "article": meta.get("article", ""),
                "filter_requested": json.dumps(trace["filter_requested"], ensure_ascii=False) if trace["filter_requested"] else "",
                "filter_applied": json.dumps(trace["filter_applied"], ensure_ascii=False) if trace["filter_applied"] else "",
                "auto_required_fields_present": all(meta.get(k) not in (None, "") for k in ("document_id", "source", "page", "document_type", "year", "section")),
                "auto_text_found_on_stated_pages": all(line[:80] in on_pages or line in on_pages for line in body if len(line) > 20) if stated else False,
                "auto_doc_fields_match_sources_json": all(meta.get(k) == v for k, v in source_meta.items()),
                "auto_in_requested_scope": "" if not trace["filter_requested"] else all(meta.get(k) == v for k, v in trace["filter_requested"].items()),
                "text_head": squash(hit["text"])[:160],
            })
    write_sheet(AUDIT / "metadata_labels.csv", rows, ["question_id", "strategy", "rank"])

    # 3) Grounding: một dòng cho mỗi (câu test, chiến lược) với đủ top-3.
    rows = []
    for trace in test:
        row = {"question_id": trace["question_id"], "strategy": trace["strategy"], "type": trace["type"],
               "question": trace["question"], "expected_answer": trace["expected_answer"],
               "gold": "; ".join(f"{g['gold_source_id']} tr.{g['gold_page']}" for g in trace["gold"]),
               "auto_hit_at_3": trace["hit_at_3"]}
        for hit in trace["top_k"]:
            row[f"top{hit['rank']}"] = f"[{hit['metadata']['document_id']} tr.{hit['metadata']['pages']} | {hit['score']}] {hit['text']}"
        rows.append(row)
    write_sheet(AUDIT / "grounding_labels.csv", rows, ["question_id", "strategy"])
    print(f"Đã ghi 3 bảng chấm vào {AUDIT}")


def cmd_audit_summary(_: argparse.Namespace) -> None:
    def load(name: str) -> list[dict[str, str]]:
        with (AUDIT / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    config = load_config()
    out: dict[str, Any] = {"generated_at": now(), "label_source_note":
                           "final = cột nhan_chot do tác giả chốt; ai_suggested = cột de_xuat_ai (chỉ để tham khảo)."}
    for column, key in (("nhan_chot", "final"), ("de_xuat_ai", "ai_suggested")):
        block: dict[str, Any] = {}
        coherence = load("coherence_labels.csv")
        grounding = load("grounding_labels.csv")
        metadata_rows = load("metadata_labels.csv")
        for strategy, strategy_config in config["strategies"].items():
            c = [int(r[column]) for r in coherence if r["index"] == strategy_config["index"] and r[column] != ""]
            g = [int(r[column]) for r in grounding if r["strategy"] == strategy and r[column] != ""]
            m = [r for r in metadata_rows if r["strategy"] == strategy]
            m_labeled = [r for r in m if r[column] != ""]
            filtered = {}
            for r in m:
                if r["filter_requested"]:
                    filtered.setdefault(r["question_id"], []).append(r["auto_in_requested_scope"] == "True")
            block[strategy] = {
                "coherence": {"n": len(c), "mean_of_2": round(sum(c) / len(c), 2) if c else None},
                "grounding": {"n": len(g), "counts_0_1_2": [g.count(0), g.count(1), g.count(2)],
                              "mean_of_2": round(sum(g) / len(g), 2) if g else None},
                "metadata": {"records_checked": len(m_labeled), "records_total": len(m),
                             "complete_and_correct": sum(r[column] == "1" for r in m_labeled)},
                "filter_compliance": {"questions_with_filter": len(filtered),
                                      "questions_top3_all_in_scope": sum(all(v) for v in filtered.values())},
            }
        out[key] = block
    write_json(AUDIT / "audit_summary.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Day 06 – pipeline ingest và benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest").set_defaults(func=cmd_ingest)
    commands.add_parser("validate").set_defaults(func=cmd_validate)
    evaluate = commands.add_parser("eval")
    evaluate.add_argument("--split", choices=["dev", "test"], required=True)
    evaluate.add_argument("--frozen", action="store_true", help="xác nhận cấu hình đã chốt trên dev")
    evaluate.set_defaults(func=cmd_eval)
    commands.add_parser("audit-sheets").set_defaults(func=cmd_audit_sheets)
    commands.add_parser("audit-summary").set_defaults(func=cmd_audit_summary)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
