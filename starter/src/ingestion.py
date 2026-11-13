"""Đọc PDF theo từng trang vật lý, chuẩn hóa text và giữ đường truy vết về nguồn/trang."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from .models import DocumentPage

# Dòng đầu trang của Công báo, ví dụ "CÔNG BÁO/Số 971 + 972/Ngày 24-7-2025" và số trang Công báo ngay sau.
RUNNING_HEADER = re.compile(r"^CÔNG BÁO/Số .+?/Ngày \d{1,2}-\d{1,2}-\d{4}$")
PAGE_NUMBER = re.compile(r"^\d{1,4}$")
# Dòng mở đầu một đơn vị cấu trúc: không được nối vào dòng trước.
STRUCTURAL_START = re.compile(
    r"^(Chương\s+[IVXLC]+\b|Mục\s+\d+\b|Điều\s+\d+\.|\d{1,2}\.\s|[a-zđ]\)\s|Mẫu số|PHỤ LỤC|Nơi nhận|Kính gửi|-\s)"
)
CHAPTER = re.compile(r"^Chương\s+([IVXLC]+)\b")
ARTICLE = re.compile(r"^Điều\s+(\d+)\.")
SENTENCE_END = re.compile(r"[.;:!?]$")
# Chú thích chữ ký số mà Cổng TTĐT Chính phủ chèn vào PDF; không thuộc nội dung văn bản.
SIGNATURE_STAMP = re.compile(r"^(Người ký:|Email:|Cơ quan:|Thời gian ký:)")
MIN_CONTENT_CHARS = 200


def normalize_text(text: str) -> str:
    """Chuẩn hóa Unicode (NFC), khoảng trắng; bỏ dòng đầu trang Công báo; nối dòng bị ngắt do dàn trang.

    Giữ lại cấu trúc: mỗi Chương/Mục/Điều/khoản/điểm nằm trên một dòng riêng.
    """
    text = unicodedata.normalize("NFC", text).replace(" ", " ").replace("­", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not SIGNATURE_STAMP.match(line)]
    # Bỏ header chạy đầu trang + số trang Công báo (chỉ trong 3 dòng đầu).
    head, body = lines[:3], lines[3:]
    head = [line for line in head if not RUNNING_HEADER.match(line) and not PAGE_NUMBER.match(line)]
    lines = head + body

    paragraphs: list[str] = []
    for line in lines:
        if (
            paragraphs
            and not STRUCTURAL_START.match(line)
            and not SENTENCE_END.search(paragraphs[-1])
            and not paragraphs[-1].isupper()  # tiêu đề chương viết hoa đứng riêng
            and not CHAPTER.match(paragraphs[-1])
            # Tên điều không kết thúc bằng dấu câu: dòng kế tiếp viết hoa là nội dung điều, không phải phần tên bị ngắt.
            and not (ARTICLE.match(paragraphs[-1]) and line[:1].isupper())
            and not re.match(r"^Mục\s+\d+$", paragraphs[-1])
        ):
            paragraphs[-1] += " " + line
        else:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def load_pdf(path: Path, document_id: str, source: str,
             doc_metadata: dict[str, Any] | None = None,
             appendix_from_page: int | None = None) -> tuple[list[DocumentPage], list[dict[str, Any]]]:
    """Trích từng trang vật lý của PDF. Trả về (các trang có nội dung, log từng trang).

    - `page` là số trang vật lý của file PDF (bắt đầu từ 1), không phải số trang Công báo.
    - Trang rỗng hoặc dưới MIN_CONTENT_CHARS ký tự được ghi vào log và KHÔNG đưa vào corpus.
    - `chapter` / `first_article` được suy từ heading trong chính văn bản, mang tiếp sang trang sau.
    - `section`: "noi_dung" hoặc "phu_luc" (từ trang `appendix_from_page` trở đi).
    """
    doc_metadata = dict(doc_metadata or {})
    pages: list[DocumentPage] = []
    log: list[dict[str, Any]] = []
    chapter = "mo_dau"
    with fitz.open(path) as pdf:
        for index, pdf_page in enumerate(pdf, start=1):
            entry: dict[str, Any] = {"document_id": document_id, "page": index}
            try:
                raw = pdf_page.get_text("text")
            except Exception as error:  # ghi lỗi, không dừng cả pipeline
                entry.update(status="error", error=repr(error), chars=0)
                log.append(entry)
                continue
            text = normalize_text(raw)
            entry.update(raw_chars=len(raw), chars=len(text))
            section = "phu_luc" if appendix_from_page and index >= appendix_from_page else "noi_dung"
            chapter_at_start = chapter
            articles = []
            for line in text.split("\n"):
                if section == "noi_dung":
                    if (m := CHAPTER.match(line)):
                        chapter = m.group(1)
                    if (m := ARTICLE.match(line)):
                        articles.append(int(m.group(1)))
            if not text:
                entry.update(status="empty", note="Không có lớp text; cần OCR nếu là trang scan")
                log.append(entry)
                continue
            if len(text) < MIN_CONTENT_CHARS:
                entry.update(status="too_short", note=f"< {MIN_CONTENT_CHARS} ký tự, không tính là trang nội dung")
                log.append(entry)
                continue
            entry.update(status="ok", section=section, chapter_at_start=chapter_at_start,
                         chapter_at_end=chapter, articles_started=articles)
            log.append(entry)
            pages.append(DocumentPage(
                document_id=document_id, source=source, page=index, text=text,
                metadata={**doc_metadata, "section": section,
                          "chapter": "phu_luc" if section == "phu_luc" else chapter_at_start,
                          "chapter_at_end": "phu_luc" if section == "phu_luc" else chapter},
            ))
    return pages, log
