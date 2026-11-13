# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.

---

## Hướng dẫn chạy bài làm cá nhân

Bài làm của **Lưu Thanh Mỹ** (MSSV 2A202603532). Báo cáo: [report/individual/2A202603532_LuuThanhMy.md](report/individual/2A202603532_LuuThanhMy.md). Nhật ký thí nghiệm: [artifacts/experiment_log.csv](artifacts/experiment_log.csv).

### 1. Yêu cầu

- Python 3.11 trở lên (bài làm dùng **Python 3.13.4**, Windows 11, CPU).
- Không cần Docker, không cần API key: model embedding và vector store chạy local.
- Lần đầu chạy cần mạng để tải model `intfloat/multilingual-e5-small` (vài trăm MB, cache lại sau đó).

### 2. Cài đặt

Chạy từ thư mục gốc của repo:

```bash
cd starter
python -m venv .venv
```

Kích hoạt môi trường: Windows PowerShell dùng `.venv\Scripts\Activate.ps1`, macOS/Linux dùng `source .venv/bin/activate`. Sau đó cài thư viện, đúng phiên bản đã dùng để chạy kết quả:

```bash
python -m pip install chromadb==1.5.9 sentence-transformers==6.3.0 torch==2.15.0 pymupdf==2.1
```

Lưu ý: `starter/requirements.txt` là khung của lớp (PostgreSQL, pypdf), không dùng cho bài làm này. Bài làm đọc PDF bằng PyMuPDF và lưu vector bằng Chroma, không dùng PostgreSQL.

### 3. Dữ liệu

- PDF nguồn nằm trong `data/raw/`:
  - `luat-91-2025-qh15.pdf` (Luật 91/2025/QH15, 25 trang)
  - `nghi-dinh-13-2023-nd-cp.pdf` (Nghị định 13/2023/NĐ-CP, 37 trang)
- Nguồn, URL, giấy phép và SHA-256 khai báo tại [data/sources.json](data/sources.json). Lệnh `ingest` kiểm tra SHA-256 trước khi đọc.
- Manifest sinh ra: [data/manifest.json](data/manifest.json) (số trang, trang được tính, quy tắc tính).
- Benchmark: [benchmark/questions.jsonl](benchmark/questions.jsonl) gồm 20 câu, 15 dev và 5 test. Cách tách: [benchmark/make_split.py](benchmark/make_split.py) (seed `20261113`). Tiêu chí chọn cấu hình: [benchmark/SELECTION.md](benchmark/SELECTION.md).

### 4. Chạy pipeline

Chạy từ thư mục `starter/`, theo đúng thứ tự:

```bash
python -m src.run ingest
python -m src.run validate
python -m src.run eval --split dev
python -m src.run eval --split test --frozen
python -m src.run audit-sheets
python -m src.run audit-summary
```

- `ingest` đọc PDF, chunk theo hai cách (A: 500 ký tự, overlap 50; B/C: theo Điều/khoản), embed và lưu vào Chroma tại `starter/chroma_db/` (không commit thư mục này).
- `validate` kiểm tra evidence của mọi câu hỏi nằm trọn trong trang gold sau chuẩn hóa.
- `eval --split test --frozen` chỉ chạy sau khi cấu hình đã chốt trên dev; cờ `--frozen` là xác nhận điều đó.
- `audit-sheets` tạo bảng chấm trong `results/audit/`. Nhãn chấm tay do tác giả điền; không chạy lại lệnh này khi không cần thiết.

Kết quả sinh ra: `results/` (chỉ số, trace top-3, chunk, log nạp lại) và `results/audit/` (bảng chấm và `audit_summary.json`).

### 5. Demo

```bash
cd starter
python demo/server.py
```

Mở **http://127.0.0.1:8000**. Trang demo được tổ chức theo năm hạng mục của rubric. Mỗi mục nêu cơ chế, số liệu lấy từ , và mã nguồn đọc trực tiếp từ module (API  dùng AST, chỉ đọc các file trong danh sách cho phép). Phần tra cứu thử chạy trên chỉ mục thật; lần đầu có thể chậm vì phải tải model.

Trang cũng giải thích vì sao bài không dùng Docker và PostgreSQL: chúng là khung của lớp, còn kết quả dùng Chroma local. Phần SQL trong trang là minh họa tương đương, không chạy trong bài.

### 6. Kết quả chính

| Cấu hình | Dev Hit@1 | Dev Hit@3 | Test Hit@1 | Test Hit@3 |
|---|---|---|---|---|
| A (cố định 500 ký tự) | 7/15 | 9/15 | 1/5 | 5/5 |
| B (theo Điều/khoản) | 8/15 | 12/15 | 4/5 | 5/5 |
| C (B + filter, được chọn) | 9/15 | 12/15 | 5/5 | 5/5 |

Test chỉ có 5 câu nên không đủ để kết luận C tốt nhất tổng quát. Chi tiết, ví dụ lỗi và giới hạn nằm trong báo cáo.

### 7. Cấu trúc

```text
starter/src/      pipeline: ingestion, chunking, embeddings, store, evaluation, run
starter/demo/     giao diện demo (server.py, index.html)
starter/config.json   cấu hình embedding, chunking, filter
data/             PDF nguồn, sources.json, manifest.json
benchmark/        câu hỏi, tách dev/test, tiêu chí chọn
results/          kết quả, trace, nhãn chấm tay
artifacts/        nhật ký thí nghiệm
report/individual/  báo cáo cá nhân
```
