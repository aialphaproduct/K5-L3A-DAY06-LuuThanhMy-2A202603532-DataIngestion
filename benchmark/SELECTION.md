# Tiêu chí chọn cấu hình — chốt TRƯỚC khi chạy tập test

Ghi lại ngày 2026-11-13, sau khi chạy dev, trước khi chạy test.

## Thứ tự ưu tiên (áp dụng trên dev)
1. Evidence Hit@3 cao nhất.
2. Nếu hòa: Hit@1 cao nhất.
3. Nếu còn hòa: ít chunk hơn (ít tài nguyên hơn).

Chất lượng grounding và coherence chỉ được chấm thủ công sau đó, không dùng để chọn cấu hình.

## Kết quả dev (results/metrics_dev.json)
| Cấu hình | Hit@1 | Hit@3 |
|---|---|---|
| A — cố định 500 ký tự | 7/15 | 9/15 |
| B — theo Điều/khoản | 8/15 | 12/15 |
| C — B + filter | 9/15 | 12/15 |

## Quyết định
- Hit@3: B = C = 12/15 > A = 9/15 → loại A.
- Hit@3 hòa giữa B và C → xét Hit@1: C (9) > B (8).
- **Cấu hình được chọn: C.**

Lưu ý: C chỉ khác B ở chỗ áp filter khi câu hỏi có filter. Trên dev, 4 câu có filter được C xử lý tốt hơn B ở Hit@1.

Tập test được chạy sau khi file này được commit, chỉ để báo cáo, không dùng để đổi tiêu chí.
