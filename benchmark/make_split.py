"""Tách dev/test MỘT LẦN, trước mọi lần chạy truy xuất.

Quy tắc: phân tầng theo việc câu hỏi có filter hay không, bốc ngẫu nhiên với seed cố định.
  - nhóm có filter (6 câu): 2 câu vào test
  - nhóm không filter (14 câu): 3 câu vào test
Kết quả: 15 dev / 5 test, test có ít nhất 1 câu filter. Chạy lại cho ra đúng cùng một phép chia.

    python benchmark/make_split.py
"""
import json
import random
from pathlib import Path

SEED = 20261113
HERE = Path(__file__).resolve().parent

questions = [json.loads(line) for line in (HERE / "questions_unsplit.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
rng = random.Random(SEED)
with_filter = sorted(q["id"] for q in questions if q.get("filter"))
without_filter = sorted(q["id"] for q in questions if not q.get("filter"))
test_ids = set(rng.sample(with_filter, 2)) | set(rng.sample(without_filter, 3))

rows = []
for question in questions:
    row = {"id": question["id"], "split": "test" if question["id"] in test_ids else "dev"}
    row.update({key: value for key, value in question.items() if key != "id"})
    rows.append(row)
(HERE / "questions.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print("seed", SEED, "| test:", sorted(test_ids), "| dev:", len(rows) - len(test_ids))
