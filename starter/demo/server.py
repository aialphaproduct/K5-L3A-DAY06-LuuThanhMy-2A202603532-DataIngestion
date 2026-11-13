"""Demo local cho Day 06: xem kết quả, truy trace, nhãn audit và tra cứu thử trên chỉ mục thật.

Chạy từ thư mục `starter/`:

    python demo/server.py          # rồi mở http://127.0.0.1:8000

Không gọi API bên ngoài. Lần tra cứu đầu tiên sẽ tải model embedding (đã cache sau đó).
"""
from __future__ import annotations

import ast
import csv
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(STARTER))

from dotenv import load_dotenv  # noqa: E402

from src.run import load_config  # noqa: E402

# Đọc starter/.env nếu có (file này đã nằm trong .gitignore). Biến đặt sẵn trong môi trường vẫn ưu tiên hơn.
load_dotenv(STARTER / ".env", override=False)

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
        "versions": stats.get("versions", {}),
        "ingest_generated_at": stats.get("generated_at"),
        "chunk_stats": {name: {"chunk_chars": info.get("chunk_chars"),
                               "tokens_total": info.get("tokens_total"),
                               "chunks_over_token_limit": info.get("chunks_over_token_limit"),
                               "seconds": info.get("seconds")}
                        for name, info in stats.get("indexes", {}).items()},
        "ingestion_log": read_json(ROOT / "results" / "ingestion_log.json", {}).get("pages_not_ok", []),
        "pages_ok": read_json(ROOT / "results" / "ingestion_log.json", {}).get("pages_ok"),
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
    steps = []
    t_engine = time.perf_counter()
    config, embedder, store = engine()
    spec = config["strategies"][strategy]
    steps.append({"step": "0. Chuẩn bị model và chỉ mục", "ms": round((time.perf_counter() - t_engine) * 1000, 1),
                  "detail": f"model {embedder.model_name} · {embedder.dimensions} chiều · "
                            f"collection day06_{spec['index']} · top_k = {config['top_k']} "
                            "(lần đầu có thể lâu vì phải tải model)"})
    clean = {}
    for key, value in filters.items():
        if key not in FILTER_KEYS or value == "":
            continue
        clean[key] = int(value) if key in INT_KEYS else value
    applied = clean if (spec["use_filter"] and clean) else None
    steps.append({"step": "1. Nhận câu hỏi", "ms": 0,
                  "detail": f"{len(question)} ký tự · câu hỏi được đưa vào truy vấn; "
                            "đáp án và nguồn gold KHÔNG được đưa vào"})

    t_embed = time.perf_counter()
    vector = embedder.embed_query(question)
    norm = sum(x * x for x in vector) ** 0.5
    steps.append({"step": "2. Mã hóa câu hỏi (E5)", "ms": round((time.perf_counter() - t_embed) * 1000, 1),
                  "detail": f"thêm tiền tố 'query: ' · vector {len(vector)} số · ‖q‖ = {norm:.4f} (đã chuẩn hóa) · "
                            f"4 số đầu: {[round(x, 4) for x in vector[:4]]}"})

    if applied:
        where_text = json.dumps({k: {"$eq": v} for k, v in applied.items()}, ensure_ascii=False)
        filter_text = f"áp where = {where_text}"
    elif not spec["use_filter"]:
        filter_text = "không áp dụng: cấu hình này không có lọc"
    else:
        filter_text = "không áp dụng: câu hỏi không có filter"
    steps.append({"step": "3. Lọc metadata", "ms": 0, "detail": filter_text})

    t_search = time.perf_counter()
    hits = store.search(spec["index"], vector, top_k=config["top_k"], filters=applied)
    steps.append({"step": "4. Tìm kiếm trong Chroma", "ms": round((time.perf_counter() - t_search) * 1000, 1),
                  "detail": f"độ đo cosine · HNSW (xấp xỉ) · {len(hits)} kết quả · "
                            f"điểm: {[h['score'] for h in hits]}"})
    steps.append({"step": "5. Xếp hạng top-k", "ms": 0,
                  "detail": "; ".join(f"#{h['rank']} {h['metadata']['document_id']} tr.{h['metadata']['pages']}"
                                      for h in hits)})
    return {
        "strategy": strategy, "index": spec["index"], "filter_requested": clean or None,
        "filter_applied": applied, "note": spec["note"], "steps": steps,
        "hits": [{"rank": h["rank"], "score": h["score"], "text": h["text"],
                  "document_id": h["metadata"]["document_id"], "source": h["metadata"]["source"],
                  "pages": h["metadata"]["pages"], "chapter": h["metadata"].get("chapter"),
                  "article": h["metadata"].get("article"), "year": h["metadata"].get("year"),
                  "document_type": h["metadata"].get("document_type"),
                  "section": h["metadata"].get("section")} for h in hits],
    }


class ServiceUnavailable(Exception):
    """Tính năng tùy chọn chưa được cấu hình (ví dụ thiếu khóa API). Không thay bằng kết quả giả."""


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANSWER_SYSTEM = (
    "Bạn trả lời câu hỏi pháp luật Việt Nam CHỈ dựa trên các đoạn được cung cấp. "
    "Nếu các đoạn không đủ thông tin, hãy nói rõ 'Không đủ bằng chứng trong các đoạn truy xuất' và không suy đoán. "
    "Trả lời ngắn gọn bằng tiếng Việt. Sau mỗi ý, ghi trích dẫn dạng [#số đoạn]."
)


def answer(question: str, strategy: str, filters: dict[str, str]) -> dict:
    """Bonus: LLM chỉ diễn giải top-3 của phần truy xuất. Không dùng cho chấm điểm retrieval hay grounding."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ServiceUnavailable(
            "Chưa đặt biến môi trường ANTHROPIC_API_KEY nên chưa gọi được LLM. "
            "Phần truy xuất vẫn chạy bình thường không cần khóa.")
    retrieved = search(question, strategy, filters)
    context = "\n\n".join(
        f"[#{h['rank']}] {h['document_id']}, trang {h['pages']}\n{h['text']}" for h in retrieved["hits"])
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
    body = {
        "model": model, "max_tokens": 900, "system": ANSWER_SYSTEM,
        "messages": [{"role": "user", "content": f"Câu hỏi: {question}\n\nCác đoạn truy xuất:\n{context}"}],
    }
    request = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    t_llm = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"API trả về lỗi HTTP {error.code}; kiểm tra khóa và hạn mức") from error
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    usage = data.get("usage", {})
    llm_steps = [{"step": "6. Gọi LLM (bonus)", "ms": round((time.perf_counter() - t_llm) * 1000, 1),
                  "detail": f"model {model} · đầu vào {len(body['messages'][0]['content'])} ký tự "
                            f"(3 đoạn truy xuất + câu hỏi) · token vào/ra: "
                            f"{usage.get('input_tokens', '?')}/{usage.get('output_tokens', '?')} · "
                            f"lý do dừng: {data.get('stop_reason', '?')}"}]
    return {"answer": text.strip(), "model": model, "retrieval": retrieved,
            "steps": retrieved["steps"] + llm_steps,
            "truncated": data.get("stop_reason") == "max_tokens",
            "note": "LLM chỉ diễn giải 3 đoạn đã truy xuất; không tham gia chấm Hit hay grounding."}


# Chỉ các file này được phép đọc qua API; không nhận đường dẫn tùy ý từ trình duyệt.
CODE_MODULES = {
    "ingestion": "src/ingestion.py", "chunking": "src/chunking.py", "embeddings": "src/embeddings.py",
    "store": "src/store.py", "evaluation": "src/evaluation.py", "run": "src/run.py", "models": "src/models.py",
}
TEXT_FILES = {
    "docker-compose.yml": "docker-compose.yml", "init.sql": "init.sql", "config.json": "config.json",
    "requirements.txt": "requirements.txt", "starter-readme": "README.md",
}


def code_symbols(module: str) -> list[dict]:
    """Trích từng hàm và lớp cấp cao nhất bằng AST: mã nguồn thật, docstring và số dòng."""
    if module not in CODE_MODULES:
        raise ValueError(f"module không được phép: {module}")
    source = (STARTER / CODE_MODULES[module]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    symbols = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            symbols.append({
                "name": node.name,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "line": node.lineno,
                "doc": ast.get_docstring(node) or "",
                "source": ast.get_source_segment(source, node) or "",
            })
    return symbols


def text_file(name: str) -> dict:
    if name not in TEXT_FILES:
        raise ValueError(f"file không được phép: {name}")
    path = (ROOT / TEXT_FILES[name]) if name == "starter-readme" else (STARTER / TEXT_FILES[name])
    return {"name": name, "content": path.read_text(encoding="utf-8")}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - tên hàm theo quy ước của http.server
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self.send_bytes((DEMO / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/style.css":
                return self.send_bytes((DEMO / "style.css").read_bytes(), "text/css; charset=utf-8")
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
            if url.path == "/api/code":
                return self.send_json({"module": query.get("module", ""),
                                       "path": CODE_MODULES.get(query.get("module", ""), ""),
                                       "symbols": code_symbols(query.get("module", ""))})
            if url.path == "/api/answer":
                filters = {k: query.get(k, "") for k in FILTER_KEYS}
                return self.send_json(answer(query.get("q", ""), query.get("strategy", "C"), filters))
            if url.path == "/api/reingest":
                return self.send_json(read_jsonl(ROOT / "results" / "reingest_log.jsonl"))
            if url.path == "/api/file":
                return self.send_json(text_file(query.get("name", "")))
            self.send_error(404)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except ServiceUnavailable as error:
            self.send_json({"error": str(error)}, status=503)
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
