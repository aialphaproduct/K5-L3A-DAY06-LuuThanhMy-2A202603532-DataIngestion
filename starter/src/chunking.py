"""Hai cách chia chunk: cửa sổ ký tự cố định (A) và theo cấu trúc Điều/khoản (B, C)."""
from __future__ import annotations

import re

from .ingestion import ARTICLE, SENTENCE_END, STRUCTURAL_START
from .models import Chunk, DocumentPage

# Các trường metadata cấp tài liệu/trang được chép sang mọi chunk.
CARRIED_FIELDS = ("document_type", "year", "doc_number", "title", "section", "chapter")
HEADING_ONLY = re.compile(r"^(Chương\s+[IVXLC]+|Mục\s+\d+)$")
CHAPTER_LINE = re.compile(r"^Chương\s+([IVXLC]+)\b")


def _base_metadata(page: DocumentPage) -> dict:
    return {key: page.metadata[key] for key in CARRIED_FIELDS if key in page.metadata}


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Cắt text của MỘT trang thành các cửa sổ `size` ký tự, chồng lấn `overlap` ký tự."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Cần size > 0 và 0 <= overlap < size")
    chunks: list[Chunk] = []
    text, step = page.text, size - overlap
    for index, start in enumerate(range(0, len(text), step)):
        piece = text[start:start + size]
        if not piece.strip():
            continue
        chunks.append(Chunk(
            chunk_id=f"{page.document_id}:fixed{size}-{overlap}:p{page.page:03d}:{index:03d}",
            document_id=page.document_id, source=page.source, page=page.page, text=piece,
            metadata={**_base_metadata(page), "page_end": page.page, "pages": str(page.page),
                      "char_start": start, "chunking": "fixed"},
        ))
        if start + size >= len(text):
            break
    return chunks


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Gom đoạn trong MỘT trang (dùng cho phụ lục/biểu mẫu, nơi không có cấu trúc Điều)."""
    chunks: list[Chunk] = []
    paragraphs = [(page.page, page.page, text) for text in page.text.split("\n")]
    for index, body in enumerate(_pack(paragraphs, max_size)):
        chunks.append(Chunk(
            chunk_id=f"{page.document_id}:struct{max_size}:p{page.page:03d}:{index:03d}",
            document_id=page.document_id, source=page.source, page=page.page,
            text="\n".join(text for *_, text in body),
            metadata={**_base_metadata(page), "page_end": page.page, "pages": str(page.page),
                      "article": 0, "chunking": "structure"},
        ))
    return chunks


def by_article(pages: list[DocumentPage], max_size: int) -> list[Chunk]:
    """Chia theo cấu trúc văn bản luật trên TOÀN BỘ một tài liệu (các trang đã sắp theo thứ tự).

    - Mỗi Điều là một đơn vị; Điều dài hơn `max_size` được tách ở ranh giới khoản/điểm,
      các phần sau lặp lại tên Điều ở đầu để chunk tự hiểu được.
    - Đoạn bị ngắt qua trang được nối lại; chunk ghi `page` (trang đầu), `page_end` và `pages`.
    - Trang phụ lục không có Điều: gom đoạn theo từng trang.
    """
    chunks: list[Chunk] = []
    content = [p for p in pages if p.metadata.get("section") != "phu_luc"]
    if content:
        first = content[0]

        # 1) Dòng các đoạn [page_start, page_end, text]; nối đoạn bị ngắt qua trang.
        stream: list[list] = []
        for page in content:
            for line_index, line in enumerate(page.text.split("\n")):
                if (line_index == 0 and stream and not STRUCTURAL_START.match(line)
                        and not SENTENCE_END.search(stream[-1][2]) and not stream[-1][2].isupper()):
                    stream[-1][1] = page.page
                    stream[-1][2] += " " + line
                else:
                    stream.append([page.page, page.page, line])

        # 2) Gom thành đơn vị theo Điều; tiêu đề Chương/Mục đi cùng Điều kế tiếp.
        units: list[dict] = []
        pending: list[list] = []
        chapter = "mo_dau"
        for start, end, text in stream:
            chapter_match = CHAPTER_LINE.match(text)
            if chapter_match or HEADING_ONLY.match(text) or (pending and text.isupper()):
                if chapter_match:
                    chapter = chapter_match.group(1)
                pending.append([start, end, text])
                continue
            article_match = ARTICLE.match(text)
            if article_match or not units:
                units.append({"article": int(article_match.group(1)) if article_match else 0,
                              "heading": text if article_match else "", "chapter": chapter,
                              "prefix": [item[2] for item in pending], "paragraphs": []})
            elif pending:  # tiêu đề đứng giữa một điều (hiếm): giữ như đoạn thường
                units[-1]["paragraphs"].extend(pending)
            pending = []
            units[-1]["paragraphs"].append([start, end, text])

        # 3) Đóng gói từng Điều thành chunk <= max_size (không tính dòng tên Điều lặp lại).
        base = {key: first.metadata[key] for key in CARRIED_FIELDS
                if key in first.metadata and key != "chapter"}
        for unit in units:
            heading = unit["heading"]
            short_heading = heading if len(heading) <= 200 else heading.split(". ", 1)[0] + "."
            for index, body in enumerate(_pack(unit["paragraphs"], max_size)):
                lines = [text for *_, text in body]
                if index == 0 and unit["prefix"]:
                    lines = [" – ".join(unit["prefix"])] + lines
                if index > 0 and short_heading:
                    lines = [f"[{short_heading} (tiếp)]"] + lines
                page_start = min(item[0] for item in body)
                page_end = max(item[1] for item in body)
                chunks.append(Chunk(
                    chunk_id=f"{first.document_id}:struct{max_size}:a{unit['article']:03d}:{index:03d}",
                    document_id=first.document_id, source=first.source, page=page_start,
                    text="\n".join(lines),
                    metadata={**base, "chapter": unit["chapter"], "page_end": page_end,
                              "pages": ",".join(str(n) for n in range(page_start, page_end + 1)),
                              "article": unit["article"], "chunking": "structure"},
                ))
    for page in pages:
        if page.metadata.get("section") == "phu_luc":
            chunks.extend(by_paragraph(page, max_size))
    return chunks


def _split_long(text: str, max_size: int) -> list[str]:
    """Tách một đoạn quá dài ở ranh giới câu/vế; cùng lắm mới cắt ở khoảng trắng."""
    if len(text) <= max_size:
        return [text]
    pieces: list[str] = []
    current = ""
    for part in re.split(r"(?<=[.;])\s+", text):
        while len(part) > max_size:  # một câu đơn dài hơn giới hạn
            if current:
                pieces.append(current)
                current = ""
            cut = part.rfind(" ", 0, max_size)
            cut = cut if cut > 0 else max_size
            pieces.append(part[:cut])
            part = part[cut:].lstrip()
        if current and len(current) + 1 + len(part) > max_size:
            pieces.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        pieces.append(current)
    return pieces


def _pack(paragraphs: list, max_size: int) -> list[list[tuple]]:
    """Gom tham lam các đoạn liên tiếp [page_start, page_end, text] thành nhóm <= max_size ký tự."""
    groups: list[list[tuple]] = []
    current: list[tuple] = []
    length = 0
    for start, end, text in paragraphs:
        for piece in _split_long(text, max_size):
            if current and length + len(piece) + 1 > max_size:
                groups.append(current)
                current, length = [], 0
            current.append((start, end, piece))
            length += len(piece) + 1
    if current:
        groups.append(current)
    return groups
