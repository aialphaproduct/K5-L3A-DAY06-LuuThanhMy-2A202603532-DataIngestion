"""Embedding model thật chạy local qua Sentence Transformers (không có fallback sang vector giả)."""
from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Trả về một vector cho mỗi văn bản, đúng thứ tự đầu vào."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Trả về vector truy vấn tương thích với vector tài liệu đã lưu."""
        ...


class MyEmbedder:
    """Adapter cho họ model E5 đa ngữ.

    Theo model card của E5: văn bản cần index thêm tiền tố "passage: ", câu truy vấn thêm "query: ";
    vector được chuẩn hóa L2. Nếu model không tải được, lỗi được ném ra chứ không thay bằng vector giả.
    """

    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 16) -> None:
        from sentence_transformers import SentenceTransformer  # import chậm, chỉ khi cần

        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device=device)
        self.dimensions = int(self.model.get_sentence_embedding_dimension())
        self.max_tokens = int(self.model.max_seq_length)
        self.document_prefix = "passage: "
        self.query_prefix = "query: "

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Số token (gồm tiền tố và token đặc biệt) để phát hiện chunk vượt giới hạn của model."""
        tokenizer = self.model.tokenizer
        return [len(tokenizer(self.document_prefix + text, truncation=False)["input_ids"]) for text in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode([self.document_prefix + text for text in texts],
                                    batch_size=self.batch_size, normalize_embeddings=True,
                                    show_progress_bar=False)
        return vectors.tolist()

    def embed_query(self, question: str) -> list[float]:
        return self.model.encode(self.query_prefix + question, normalize_embeddings=True).tolist()
