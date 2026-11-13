"""Demo local cho Day 06: xem kết quả, truy trace, nhãn audit và tra cứu thử trên chỉ mục thật.

Chạy từ thư mục `starter/`:

    python demo/server.py          # rồi mở http://127.0.0.1:8000

Không gọi API bên ngoài. Lần tra cứu đầu tiên sẽ tải model embedding (đã cache sau đó).
"""
from __future__ import annotations

import csv
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(STARTER))

from src.run import load_config  # noqa: E402

HOST, PORT = "127.0.0.1", 8000
FILTER_KEYS = {"document_type", "year", "section"}
INT_KEYS = {"year"}

_engine_lock = threading.Lock()
_engine = None


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def overview() -> dict:
    config = load_config()
    stats = read_json(ROOT / "results" / "ingest_stats.json", {})
    manifest = read_json(ROOT / "data" / "manifest.json", {})
    dev = read_json(ROOT / "results" / "metrics_dev.json", {"rows": []})
    test = read_json(ROOT / "results" / "metrics_test.json", {"rows": []})
    experiments = read_csv(ROOT / "artifacts" / "experiment_log.csv")
    documents = [
        {"title": d["title"], "physical_pages": d["physical_pages"],
         "content_pages": len(d["content_pages_counted"]), "license": d["license_or_permission"]}
        for d in manifest.get("documents", [])
    ]
    return {
        "content_pages_total": manifest.get("content_pages_total"),
        "minimum_required": manifest.get("minimum_required"),
        "documents": documents,
        "embedding_model": config["embedding_model"],
        "indexes": {name: {"chunks": info["chunk_count"], "config": info["config"]}
                    for name, info in stats.get("indexes", {}).items()},
        "strategies": config["strategies"],
        "chosen": "C",
        "cost_usd": stats.get("cost_usd", 0),
        "dev": dev.get("rows", []),
        "test": test.get("rows", []),
        "experiments": experiments,
    }


def questions() -> list[dict]:
    rows = read_jsonl(STARTER.parent / "benchmark" / "questions.jsonl")
    return [{"id": q["id"], "split": q["split"], "question": q["question"], "type": q.get("type"),
             "expected_answer": q["expected_answer"], "filter": q.get("filter"),
             "filter_context": q.get("filter_context")} for q in rows]


def traces(split: str) -> list[dict]:
    rows = read_jsonl(ROOT / "results" / f"trace_{split}.jsonl")
    return [{
        "question_id": t["question_id"], "strategy": t["strategy"], "split": t["split"],
        "question": t["question"], "expected_answer": t["expected_answer"], "gold": t["gold"],
        "hit_at_1": t["hit_at_1"], "hit_at_3": t["hit_at_3"], "failure_type": t["failure_type"],
        "filter_requested": t["filter_requested"], "filter_applied": t["filter_applied"],
        "top_k": [{"rank": h["rank"], "score": h["score"], "is_hit": h.get("is_hit", False),
                   "document_id": h["metadata"]["document_id"], "pages": h["metadata"]["pages"],
                   "chapter": h["metadata"].get("chapter"), "text": h["text"]} for h in t["top_k"]],
    } for t in rows]


def audit() -> dict:
    coherence = read_csv(ROOT / "results" / "audit" / "coherence_labels.csv")
    metadata = read_csv(ROOT / "results" / "audit" / "metadata_labels.csv")
    grounding = read_csv(ROOT / "results" / "audit" / "grounding_labels.csv")
    return {
        "coherence": [{"position": r["position_id"], "index": r["index"], "page": r["page"],
                       "document_id": r["document_id"], "score": r["nhan_chot"],
                       "chunk_text": r["chunk_text"][:500]} for r in coherence],
        "metadata": [{"question_id": r["question_id"], "strategy": r["strategy"], "rank": r["rank"],
                      "document_id": r["document_id"], "pages": r["pages"], "chapter": r["chapter"],
                      "score": r["nhan_chot"]} for r in metadata],
        "grounding": [{"question_id": r["question_id"], "strategy": r["strategy"],
                       "question": r["question"], "expected_answer": r["expected_answer"],
                       "score": r["nhan_chot"]} for r in grounding],
    }


def engine():
    """Tải model và chỉ mục một lần; các lần sau dùng lại."""
    global _engine
    with _engine_lock:
        if _engine is None:
            from src.embeddings import MyEmbedder
            from src.store import ChromaStore

            config = load_config()
            embedder = MyEmbedder(config["embedding_model"])
            store = ChromaStore(str(STARTER / config["chroma_dir"]), embedder.dimensions, embedder.model_name)
            _engine = (config, embedder, store)
        return _engine


def search(question: str, strategy: str, filters: dict[str, str]) -> dict:
    if strategy not in ("A", "B", "C"):
        raise ValueError("strategy phải là A, B hoặc C")
    if not question.strip():
        raise ValueError("Câu hỏi đang trống")
    config, embedder, store = engine()
    spec = config["strategies"][strategy]
    clean = {}
    for key, value in filters.items():
        if key not in FILTER_KEYS or value == "":
            continue
        clean[key] = int(value) if key in INT_KEYS else value
    applied = clean if (spec["use_filter"] and clean) else None
    vector = embedder.embed_query(question)
    hits = store.search(spec["index"], vector, top_k=config["top_k"], filters=applied)
    return {
        "strategy": strategy, "index": spec["index"], "filter_requested": clean or None,
        "filter_applied": applied, "note": spec["note"],
        "hits": [{"rank": h["rank"], "score": h["score"], "text": h["text"],
                  "document_id": h["metadata"]["document_id"], "source": h["metadata"]["source"],
                  "pages": h["metadata"]["pages"], "chapter": h["metadata"].get("chapter"),
                  "article": h["metadata"].get("article"), "year": h["metadata"].get("year"),
                  "document_type": h["metadata"].get("document_type"),
                  "section": h["metadata"].get("section")} for h in hits],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - tên hàm theo quy ước của http.server
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self.send_bytes((DEMO / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/overview":
                return self.send_json(overview())
            if url.path == "/api/questions":
                return self.send_json(questions())
            if url.path == "/api/traces":
                return self.send_json(traces(query.get("split", "dev")))
            if url.path == "/api/audit":
                return self.send_json(audit())
            if url.path == "/api/search":
                filters = {k: query.get(k, "") for k in FILTER_KEYS}
                return self.send_json(search(query.get("q", ""), query.get("strategy", "C"), filters))
            self.send_error(404)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except Exception as error:  # hiển thị lỗi thật, không che bằng dữ liệu giả
            self.send_json({"error": f"{type(error).__name__}: {error}"}, status=500)

    def send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - giữ chữ ký gốc
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Demo đang chạy tại http://{HOST}:{PORT}  (Ctrl+C để dừng)")
    server.serve_forever()


if __name__ == "__main__":
    main()
