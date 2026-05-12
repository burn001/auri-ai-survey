"""phase2b release 풀 → wave4.json (50 sub-batch).

5/13 사용자 결정: 설계 quota 도달 → 추가 발송에서 설계 제외.
phase2b 풀은 모두 category=유지관리지만 안전망으로 category != 설계 필터 적용.

dedup: send_pending_invites.py 가 email_sent=true 토큰 자동 제외 (이미 동작).
Gmail 일일 한도 ~500 — 4,987을 wave4.json 에 일괄 적재하고 schtask 가 매일 12:00 trigger,
quota abort 시 다음 회차로 자동 이어받음 (send_pending_invites.py exit code 2).
"""
import builtins
import json
import os
import sys
from datetime import datetime
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_orig_print = builtins.print
def _eprint(*a, **kw):
    kw.setdefault("file", sys.stderr)
    return _orig_print(*a, **kw)
builtins.print = _eprint

from pymongo import MongoClient

URI = os.getenv(
    "MONGO_URL",
    "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@mongod:27017/?authSource=admin&directConnection=true",
)
DB = "auri_survey"
RELEASE_FLAG = "phase2b_2026_05_13_fm_society"
SUB_BATCH = 50
OUT = Path(os.getenv("OUT_PATH", "/tmp/wave4.json"))

c = MongoClient(URI)
db = c[DB]

cursor = db.participants.find(
    {
        "email_release_phase": RELEASE_FLAG,
        "email_sent": {"$ne": True},
        "bounced": {"$ne": True},
        "email_invalid": {"$ne": True},
        "category": {"$ne": "설계"},
        "source": {"$ne": "staff"},
    },
    {"_id": 0, "token": 1, "name": 1, "email": 1, "category": 1, "source": 1, "org": 1},
).sort([("source", 1), ("token", 1)])

items = list(cursor)
print(f"matched: {len(items)}")

by_src = {}
by_cat = {}
for d in items:
    by_src[d.get("source")] = by_src.get(d.get("source"), 0) + 1
    by_cat[d.get("category")] = by_cat.get(d.get("category"), 0) + 1
print(f"by source: {by_src}")
print(f"by category: {by_cat}")

tokens = [d["token"] for d in items]
batches = [tokens[i:i + SUB_BATCH] for i in range(0, len(tokens), SUB_BATCH)]

out = {
    "wave": 4,
    "size": len(tokens),
    "sub_batch_size": SUB_BATCH,
    "sub_batches": len(batches),
    "release_flag": RELEASE_FLAG,
    "exclude_category": ["설계"],
    "built_at": datetime.now().isoformat(timespec="seconds"),
    "tokens": batches,
    "manifest": [
        {
            "token": d["token"],
            "name": d.get("name") or "",
            "email": d.get("email") or "",
            "category": d.get("category"),
            "source": d.get("source"),
            "org": d.get("org") or "",
        }
        for d in items
    ],
}
out_json = json.dumps(out, ensure_ascii=False, indent=2)
if str(OUT) == "-":
    _orig_print(out_json)
    print(f"wave4.json -> stdout ({len(batches)} sub-batches x {SUB_BATCH})")
else:
    OUT.write_text(out_json, encoding="utf-8")
    print(f"wave4.json written to {OUT}: {len(batches)} sub-batches x {SUB_BATCH}")

c.close()
