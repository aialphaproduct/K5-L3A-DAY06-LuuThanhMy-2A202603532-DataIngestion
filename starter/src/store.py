"""Lưu và tìm kiếm vector bằng Chroma (PersistentClient, lưu bền vững trên đĩa)."""
from __future__ import annotations

from typing import Any

import chromadb

from .models import Chunk

# Chỉ các trường này được phép dùng làm filter; giá trị luôn đi qua tham số `where` của Chroma.
FILTERABLE_FIELDS = {"document_id", "document_type", "year", "section", "chapter", "article"}


class ChromaStore:
    def __init__(self, path: str, dimensions: int, embedding_model: str) -> None:
        self.path = path
        self.dimensions = dimensions
        self.embedding_model = embedding_model
        self.client = chromadb.PersistentClient(path=path)

    def _collection(self, index: str):
        """Một collection cho mỗi chỉ mục chunk; metric cosine, chỉ mục HNSW (xấp xỉ) của Chroma."""
        collection = self.client.get_or_create_collection(
            name=f"day06_{index}",
            metadata={"hnsw:space": "cosine", "embedding_model": self.embedding_model,
                      "dimensions": self.dimensions},
        )
        stored = collection.metadata or {}
        if stored.get("embedding_model") != self.embedding_model or stored.get("dimensions") != self.dimensions:
            raise RuntimeError(
                f"Collection day06_{index} được tạo bằng model/số chiều khác ({stored}); "
                "hãy xóa thư mục chroma_db rồi ingest lại thay vì trộn vector không tương thích.")
        return collection

    def count(self, index: str) -> int:
        return self._collection(index).count()

    def upsert(self, index: str, chunks: list[Chunk], vectors: list[list[float]]) -> dict[str, int]:
        """Ghi theo ID ổn định (nạp lại không nhân đôi) và xóa chunk cũ không còn trong lần nạp này."""
        if len(chunks) != len(vectors):
            raise ValueError("Số chunk và số vector không khớp")
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ValueError(f"Vector không đúng {self.dimensions} chiều")
        ids = [chunk.chunk_id for chunk in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("chunk_id bị trùng trong cùng một lần nạp")
        collection = self._collection(index)
        before = collection.count()
        existing = set(collection.get(include=[])["ids"])
        obsolete = sorted(existing - set(ids))
        if obsolete:
            collection.delete(ids=obsolete)
        metadatas = [{"document_id": c.document_id, "source": c.source, "page": c.page, **c.metadata}
                     for c in chunks]
        for start in range(0, len(ids), 200):
            end = start + 200
            collection.upsert(ids=ids[start:end], embeddings=vectors[start:end],
                              documents=[c.text for c in chunks[start:end]], metadatas=metadatas[start:end])
        return {"before": before, "after": collection.count(), "written": len(ids),
                "new": len(set(ids) - existing), "deleted_obsolete": len(obsolete)}

    def search(self, index: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Lọc metadata ngay trong truy vấn của Chroma, xếp theo khoảng cách cosine, trả top-k."""
        where = None
        if filters:
            unknown = set(filters) - FILTERABLE_FIELDS
            if unknown:
                raise ValueError(f"Trường filter không được phép: {sorted(unknown)}")
            clauses = [{key: {"$eq": value}} for key, value in filters.items()]
            where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        result = self._collection(index).query(
            query_embeddings=[query_vector], n_results=top_k, where=where,
            include=["documents", "metadatas", "distances"])
        hits = []
        rows = zip(result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0])
        for rank, (chunk_id, text, metadata, distance) in enumerate(rows, start=1):
            hits.append({"rank": rank, "chunk_id": chunk_id, "score": round(1.0 - float(distance), 6),
                         "text": text, "metadata": metadata})
        return hits
