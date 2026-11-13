
# Báo cáo cá nhân — Day 6

Họ tên: Lưu Thanh Mỹ · MSSV: 2A202603532 

Trạng thái: **kết quả tự động và nhãn thủ công (coherence, metadata, grounding) đã đầy đủ. Nhãn do tác giả chấm và chốt, sau khi tham khảo gợi ý của AI ở một số dòng.** Mọi con số dưới đây lấy từ file trong `results/`, không ước tính.

## Dữ liệu và pipeline
- Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:
  - Chủ đề: bảo vệ dữ liệu cá nhân tại Việt Nam, gồm hai văn bản: **Luật 91/2025/QH15** (25 trang PDF) và **Nghị định 13/2023/NĐ-CP** (37 trang PDF).
  - Nguồn: Công báo điện tử Chính phủ (`congbaocdn.chinhphu.vn`), bản PDF gốc, truy cập ngày 2026-11-13. SHA-256 và URL ghi trong `data/sources.json`.
  - Quyền sử dụng: tác giả đánh giá đây là văn bản quy phạm pháp luật đăng công khai trên Công báo, không thuộc đối tượng bảo hộ quyền tác giả theo Điều 15 Luật Sở hữu trí tuệ. Đây là nhận định của tác giả, chưa được tư vấn pháp lý.
  - Quy tắc tính trang: `page` là số trang vật lý của PDF (bắt đầu từ 1), không phải số trang in trên Công báo. Trang nội dung là trang thuộc phần văn bản chính (`section = noi_dung`) và trích được ≥200 ký tự sau chuẩn hóa. Phụ lục biểu mẫu (Nghị định, trang 30–37, **8 trang**) được index nhưng **không tính** vào số trang nội dung.
  - Kết quả: **62 trang vật lý, 62 trang dùng được, 54 trang nội dung được tính** (Luật 25, Nghị định 29). Yêu cầu tối thiểu là 30.
- Chuẩn hóa, lỗi và giới hạn:
  - Chuẩn hóa Unicode NFC, bỏ khoảng trắng thừa, bỏ dòng tiêu đề chạy của Công báo, bỏ dòng chữ ký số, nối các dòng bị ngắt do dàn trang; giữ nguyên ranh giới Chương/Mục/Điều/khoản/điểm.
  - **0 trang lỗi, 0 trang rỗng** (xem `results/ingestion_log.json`).
  - Giới hạn: chỉ đọc lớp text, **không OCR**. Phụ lục biểu mẫu là text có dấu chấm và gạch, không phải bảng; trích xuất không đảm bảo giữ đúng bố cục. Dấu gạch nối ở cuối dòng bị ngắt có thể còn sót ở vài chỗ.
- Embedding model/provider/version/số chiều, vector store/version, metric, lệnh chạy và chi phí:
  - Model: **`intfloat/multilingual-e5-small`**, **384 chiều**, giới hạn **512 token**, chạy local trên CPU, không cần API key. Tiền tố `passage: ` khi index, `query: ` khi truy vấn; vector chuẩn hóa L2.
  - Vector store: **ChromaDB 1.5.9**, `PersistentClient` lưu trên đĩa; metric cosine; chỉ mục HNSW (tìm kiếm xấp xỉ, mặc định của Chroma).
  - Phiên bản: Python 3.13.4, sentence-transformers 6.3.0, torch 2.15.0, transformers 5.22.2, PyMuPDF 2.1, numpy 2.5.4 (`results/ingest_stats.json`).
  - Lệnh chạy, từ thư mục `starter/`:
    ```bash
    python -m src.run ingest
    python -m src.run validate
    python -m src.run eval --split dev
    python -m src.run eval --split test --frozen
    python -m src.run audit-sheets
    ```
  - Chi phí: **0 USD** (model và store chạy local). Thời gian build chỉ mục: **~10.7 giây** cho mỗi chỉ mục (embed chiếm gần hết), trên máy Windows 11.
  - Nạp lại: chạy `ingest` hai lần. Lần hai ghi **`new: 0`, `deleted_obsolete: 0`** cho cả hai chỉ mục (290 và 172 chunk), tức không nhân đôi. Xem `results/reingest_log.jsonl`.
- Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:
  - Bắt buộc cho mỗi chunk: `document_id`, `source`, `page`, `pages` (các trang chunk phủ), `page_end`.
  - Trường lọc: **`document_type`** (`luat` / `nghi_dinh`), **`year`** (2025 / 2023), **`section`** (`noi_dung` / `phu_luc`). Ngoài ra có `chapter` và `article` làm thông tin truy vết (không dùng để lọc ở C).
  - Nguồn giá trị: khai báo tay trong `data/sources.json` theo số ký hiệu và năm ban hành của văn bản, không suy từ nội dung chunk.
  - Lý do chọn: ba trường này có giá trị phân biệt rõ trong corpus (2 loại văn bản, 2 năm, phụ lục và thân văn bản).
  - Lưu ý: filter trong câu hỏi là **input giả định cung cấp trước khi truy xuất**, mô tả tại `filter_context` của từng câu trong `benchmark/questions.jsonl`. Đáp án và nguồn gold không đưa vào filter.

## Cấu hình và benchmark
- A / B / C:
  - **A**: chunk cố định **500 ký tự, overlap 50**, cắt trong từng trang; **290 chunk**; không lọc.
  - **B**: chunk theo **Điều/khoản**, tối đa **1000 ký tự**, nối đoạn bị ngắt qua trang; **172 chunk**; không lọc.
  - **C**: cùng chỉ mục với B; **chỉ thêm lọc metadata khi câu hỏi có filter**.
  - Khác biệt B–C: chỉ ở bước truy vấn (lọc). Khác biệt A–B: chunking, và B có thêm trường `article`/`chapter` trong metadata. Vì vậy so sánh A–B không hoàn toàn "thay một yếu tố"; cần ghi rõ khi đọc kết quả.
  - Cấu hình đầy đủ: `starter/config.json`.
- ≥20 câu: phân chia ≥15 dev / ≥5 test, câu metadata filter và cách kiểm chứng evidence:
  - **20 câu: 15 dev / 5 test**. Phân chia bằng seed cố định (`20261113`) theo hai nhóm (có filter / không filter), trong `benchmark/make_split.py`.
  - Test: q03, q06, q14, q15, q16. Dev: 15 câu còn lại.
  - **6 câu có filter** (dev 4, test 2), đạt yêu cầu ≥3 câu có filter và ≥1 câu test có filter.
  - Kiểm chứng evidence: `python -m src.run validate` kiểm tra nguyên văn evidence nằm trọn trong trang gold sau chuẩn hóa, cho cả nguồn thay thế (`alt_gold`). Kết quả: **20/20 câu đạt, không có vấn đề** (`results/benchmark_validation.json`).
  - Benchmark đã commit (`2c3cf10`) **trước** khi chạy truy xuất.
- Tiêu chí chọn chiến lược đã chốt trước khi xem test:
  - Thứ tự: (1) Evidence Hit@3 trên dev; (2) hòa thì Hit@1; (3) hòa thì ít chunk hơn.
  - Chốt trong `benchmark/SELECTION.md`, commit **`7e84cfa`**, trước khi chạy tập test.
  - Kết quả dev: Hit@3 B = C = 12/15 > A = 9/15 → loại A; hòa Hit@3 → Hit@1: C (9) > B (8) → **chọn C**.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian (build chỉ mục / truy vấn trung bình) | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 7/15 (46,7%) | 9/15 (60,0%) | 290 | 10,7 s / 31,5 ms | 0 USD |
| B | dev | 8/15 (53,3%) | **12/15 (80,0%)** | 172 | 10,7 s / 29,6 ms | 0 USD |
| **C** | dev | **9/15 (60,0%)** | **12/15 (80,0%)** | 172 (dùng chung B) | 10,7 s / 27,3 ms | 0 USD |
| A | test | 1/5 (20,0%) | 5/5 (100%) | 290 | 10,7 s / 37,8 ms | 0 USD |
| B | test | 4/5 (80,0%) | 5/5 (100%) | 172 | 10,7 s / 35,3 ms | 0 USD |
| **C** | test | **5/5 (100%)** | 5/5 (100%) | 172 (dùng chung B) | 10,7 s / 25,9 ms | 0 USD |

Ghi chú: "Thời gian" đo trên máy tác giả, CPU; số mẫu nhỏ nên không nên so sánh tốc độ chi tiết.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1,0 (n=10) | 15/15 | N/A (không áp filter) | 0/0/5, TB 2,0 |
| B | 1,8 (n=10) | 15/15 | N/A (không áp filter) | 0/0/5, TB 2,0 |
| C | 1,8 (n=10) (dùng chung chỉ mục với B) | 15/15 | **test 2/2 câu có filter, top-3 trong phạm vi**; dev 4/4 | 0/0/5, TB 2,0 |

Ghi chú về bảng thứ hai:
- Coherence: tổng hợp 20 dòng là **1,4/2**. Mọi chunk cố định (A) đều bị cắt giữa ý ở cả 10 vị trí. Chunk theo Điều (B/C) đạt 2 ở 8/10 vị trí, và 1 ở 2 vị trí có chunk bắt đầu bằng khoản lẻ hoặc danh mục.
- Metadata: 45/45 bản ghi đúng. Riêng hai bản ghi q15 và q16 của cấu hình A ở rank 3 nằm trên trang 29 của Nghị định, có tiêu đề Chương IV xuất hiện giữa chunk trong khi `chapter` ghi theo đầu chunk (Chương III). Tác giả chấm 1 vì chunk bắt đầu trong Chương III; quy ước này được ghi ở phần hạn chế.
- Grounding: tác giả chấm 2 cho cả 15 dòng. Điều này trùng với gợi ý AI, nên thang điểm có thể dễ dãi; xem phần hạn chế.
- Filter test C: 2/2 câu, cả top-3 đều thuộc đúng `document_type`/`year`/`section` được yêu cầu.

Đường dẫn trace, nhãn thủ công và cách chọn mẫu:
- Trace đầy đủ top-3 (text, score, metadata, trường hợp lỗi): `results/trace_dev.jsonl`, `results/trace_test.jsonl`.
- Chỉ số: `results/metrics_dev.json`, `results/metrics_test.json`.
- Chunk đã index: `results/chunks_fixed.jsonl`, `results/chunks_struct.jsonl`.
- Bảng chấm thủ công: `results/audit/coherence_labels.csv` (10 vị trí được chọn theo quy tắc cố định: trang nội dung thứ 3, 8, 13… sau khi sắp theo tài liệu và trang, trước khi xem điểm), `results/audit/metadata_labels.csv` (toàn bộ top-3 của 5 câu test × 3 cấu hình), `results/audit/grounding_labels.csv` (5 câu test × 3 cấu hình).

## Phân tích và kết luận
- So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn:
  - **A–B (chunking)**: B đạt Hit@3 cao hơn A trên dev (12 so với 9). Ví dụ **q17** (dev, "phải thông báo cho ai và trong thời hạn nào"; đáp án ở Nghị định 13/2023, trang 18): A trả về **toàn bộ 3 chunk từ Luật 91/2025 (Điều 23)**, sai tài liệu vì hai văn bản có Điều 23 gần giống nhau. B đưa được chunk Nghị định vào top-2. Lỗi thuộc loại `sai_tai_lieu`.
  - **B–C (filter)**: C đạt Hit@1 cao hơn B trên dev (9 so với 8). Với **q17**, khi áp `year = 2023` (filter giả định cung cấp trước), C đưa toàn bộ top-3 về Nghị định và đặt chunk đúng ở **rank 1**. Đây là trường hợp lọc metadata làm đúng việc của nó.
  - Ví dụ thất bại chung của B và C: **q10** (dev, "đã đồng ý theo quy định cũ thì có phải xin đồng ý lại?"; đáp án là Điều 39 Luật 91/2025, trang 25, khoản chuyển tiếp). Cả B và C đều trả về Điều 10–11 (rút lại và đồng ý), không có chunk chuyển tiếp trong top-3. Đây là lỗi truy xuất ngữ nghĩa: câu hỏi dùng từ "quy định cũ" không khớp với văn bản chuyển tiếp.
  - **Coherence và metadata**: chunk cố định (A) cắt giữa câu ở mọi vị trí được chấm, nên điểm coherence thấp hơn rõ rệt (1,0 so với 1,8 của B/C). Metadata đúng ở cả ba cấu hình, nên khác biệt giữa các cấu hình nằm ở nội dung chunk chứ không ở metadata.
- Chiến lược chọn, đánh đổi, điều kiện áp dụng:
  - Chọn **C**: Hit@3 dev ngang B (12/15), Hit@1 dev cao hơn (9 so với 8), và trên test đạt **5/5** cho cả Hit@1 và Hit@3.
  - Đánh đổi: C chỉ hơn B khi câu hỏi có filter và filter đúng. Nếu người dùng cung cấp filter sai, C có thể loại bỏ chunk đúng. Lỗi này **chưa được kiểm thử** trong benchmark hiện tại.
  - Điều kiện áp dụng: corpus có ít nhất một trường lọc có giá trị phân biệt và giá trị lọc đến từ ngữ cảnh người dùng, không suy từ câu hỏi.
- Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:
  - **Test chỉ 5 câu** (cộng với 15 dev). Kết quả 5/5 của C **không chứng minh C tốt nhất tổng quát**; khoảng tin cậy rất rộng.
  - Không dùng test để chỉnh sửa: tiêu chí đã commit trước khi chạy test. Tác giả đã xem kết quả test sau khi chốt tiêu chí, không đổi quyết định.
  - Nhãn thủ công do **một người (tác giả) chấm**, không có người chấm thứ hai để kiểm chứng. Tác giả đã tham khảo gợi ý AI ở một số dòng, nên có nguy cơ thiên lệch.
  - Grounding: tác giả chấm **2 cho cả 15 dòng**. Thang điểm này có thể dễ dãi, đặc biệt ở q16 cấu hình A và B, nơi top-3 lẫn cả hai văn bản.
  - Coherence: thang 0/1/2 có tính chủ quan; 10 vị trí chỉ là mẫu nhỏ.
  - Metadata: quy ước `chapter` lấy theo đầu chunk, nên với chunk trải qua ranh giới chương vẫn được coi là đúng. Nếu lấy quy ước khác, hai bản ghi q15 và q16 của A sẽ thành 0 và điểm metadata A còn 13/15.
  - Lý do chấm của tác giả không còn đầy đủ trong file vì lỗi mã hóa khi lưu, nên báo cáo dựa vào điểm số.
  - Corpus chỉ có hai văn bản cùng lĩnh vực, nhiều điều khoản lặp lại gần như nguyên văn giữa Luật và Nghị định. Điều này tạo ra lỗi `sai_tai_lieu` mà corpus khác có thể không gặp.
  - Chỉ có text layer, không OCR; phụ lục biểu mẫu chưa được kiểm tra bố cục.
  - Đo thời gian trên một máy CPU, một lần chạy; không đo chi phí vì model chạy local.
  - Một phần cấu hình A khác B ở metadata (không chỉ chunking), nên kết luận A–B cần đọc thận trọng.
- Nếu làm lại hoặc mở rộng, cải tiến gì?
  - Thêm câu test để tăng cỡ mẫu (mục tiêu ≥10 câu test), và thêm câu có filter sai để kiểm thử hành vi khi filter không khớp.
  - Tách hẳn A và B chỉ theo chunking (giữ nguyên metadata), để so sánh A–B đúng nghĩa "thay một yếu tố".
  - Thử thêm hybrid retrieval (BM25 + dense) cho các câu dùng từ ngữ khác với văn bản như q10.
  - OCR cho các văn bản scan và kiểm tra bố cục phụ lục.
