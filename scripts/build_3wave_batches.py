"""5/6·5/7·5/8 3회차 분배 산출 (옵션 X).

대상: source != 'staff' + category ∈ {설계, 시공, 유지관리, 건축행정} + email_sent != true
정책:
- wave 크기 [250, 450, 453] (5/6 / 5/7 / 5/8)
- 직군별 round-robin (4직군 균등 분포)
- 각 직군 내에서 Phase 2A 신규(`email_release_phase = phase2a_2026_05_04`) 우선
- 각 wave 를 50개 sub-batch 로 분할 → SMTP rate-limit 안전선 유지

산출: wave1.json / wave2.json / wave3.json (tokens·manifest)
"""
import json
import sys
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

from pymongo import MongoClient

URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true"
DB = "auri_survey"

WAVE_SIZES = {1: 250, 2: 450, 3: 453}  # 5/6, 5/7, 5/8
PHASE2A_FLAG = "phase2a_2026_05_04"
CATEGORIES = ["설계", "시공", "유지관리", "건축행정"]
SUB_BATCH_SIZE = 50

OUT_DIR = Path(__file__).parent

c = MongoClient(URI)
db = c[DB]

cursor = db.participants.find(
    {
        "source": {"$ne": "staff"},
        "category": {"$in": CATEGORIES},
        "email_sent": {"$ne": True},
    },
    {
        "_id": 0,
        "token": 1, "name": 1, "email": 1, "org": 1, "phone": 1,
        "category": 1, "source": 1,
        "email_release_phase": 1, "created_at": 1,
    },
)
data = list(cursor)
total = len(data)
expected = sum(WAVE_SIZES.values())
print(f"잔여 발송 풀 추출: {total} (예상 {expected})")
if total != expected:
    print(f"⚠ 잔여 풀 크기 불일치 — 실제 {total} != 예상 {expected}. wave size 재조정 필요할 수 있음.")

# Phase 2A 우선 sort key
def sort_key(d):
    is_p2a = d.get("email_release_phase") == PHASE2A_FLAG
    created = d.get("created_at") or datetime.min
    return (0 if is_p2a else 1, created)

groups = {}
for cat in CATEGORIES:
    items = [d for d in data if d.get("category") == cat]
    groups[cat] = sorted(items, key=sort_key)
    p2a = sum(1 for d in items if d.get("email_release_phase") == PHASE2A_FLAG)
    print(f"  {cat}: {len(items)} (Phase 2A {p2a})")

# round-robin interleave
interleaved = []
for tup in zip_longest(*groups.values(), fillvalue=None):
    for x in tup:
        if x is not None:
            interleaved.append(x)
assert len(interleaved) == total, f"interleave 손실: {len(interleaved)} != {total}"

# wave 분리
waves = {}
idx = 0
for wave_num, size in WAVE_SIZES.items():
    chunk = interleaved[idx:idx + size]
    actual = len(chunk)
    if actual < size:
        print(f"⚠ wave {wave_num}: 요청 {size}, 실제 {actual} (잔여 풀 부족)")
    waves[wave_num] = chunk
    idx += actual

# 통계 + 산출물 저장
print(f"\n=== wave 별 분포 ===")
for wave_num, items in waves.items():
    cats = {}
    p2a = 0
    for d in items:
        cats[d["category"]] = cats.get(d["category"], 0) + 1
        if d.get("email_release_phase") == PHASE2A_FLAG:
            p2a += 1
    print(f"wave {wave_num} ({len(items)}): Phase 2A {p2a} / 직군 {cats}")

    sub_batches = [items[i:i + SUB_BATCH_SIZE] for i in range(0, len(items), SUB_BATCH_SIZE)]
    out = {
        "wave": wave_num,
        "size": len(items),
        "sub_batch_size": SUB_BATCH_SIZE,
        "sub_batches": len(sub_batches),
        "tokens": [[d["token"] for d in b] for b in sub_batches],
        "manifest": [
            {
                "token": d["token"],
                "name": d.get("name", ""),
                "email": d.get("email", ""),
                "org": d.get("org", ""),
                "category": d.get("category"),
                "source": d.get("source"),
                "release_phase": d.get("email_release_phase", ""),
                "phase2a": d.get("email_release_phase") == PHASE2A_FLAG,
            }
            for d in items
        ],
    }
    out_path = OUT_DIR / f"wave{wave_num}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → 저장: {out_path.name} ({len(sub_batches)} × {SUB_BATCH_SIZE} sub-batch)")

c.close()
