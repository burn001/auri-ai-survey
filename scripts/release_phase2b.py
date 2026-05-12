"""bulk_import 4,987 (FM학회 회원·법인회원사) 활성 발송 풀로 풀어줌.

5/4 import 시 `email_sent=true` + `email_skip_reason='bulk_import_2026_05_04_pending_review'`
로 차단해뒀던 풀을, 5/13 사용자 결정(설계 quota 도달 → 유지관리 sample 확보)에 따라 해제.

매핑 변경:
  email_sent: true -> false
  email_skip_reason: 'bulk_import_2026_05_04_pending_review' -> 제거
  email_release_phase: 'phase2b_2026_05_13_fm_society' 추가

DRY_RUN 기본. --apply 인자 또는 DRY_RUN=0 환경변수로 실제 적용.
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")

from pymongo import MongoClient

URI = os.getenv(
    "MONGO_URL",
    "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@mongod:27017/?authSource=admin&directConnection=true",
)
DB = "auri_survey"
DRY_RUN = (os.getenv("DRY_RUN", "1") == "1") and ("--apply" not in sys.argv)

FILTER = {"email_skip_reason": "bulk_import_2026_05_04_pending_review"}
UPDATE = {
    "$set": {"email_sent": False, "email_release_phase": "phase2b_2026_05_13_fm_society"},
    "$unset": {"email_skip_reason": ""},
}

c = MongoClient(URI)
db = c[DB]

n = db.participants.count_documents(FILTER)
print(f"matched: {n}")

print("by source:")
for d in db.participants.aggregate([
    {"$match": FILTER},
    {"$group": {"_id": "$source", "n": {"$sum": 1}}},
    {"$sort": {"n": -1}},
]):
    print(f"  {d['_id']}: {d['n']}")

print("by category:")
for d in db.participants.aggregate([
    {"$match": FILTER},
    {"$group": {"_id": "$category", "n": {"$sum": 1}}},
]):
    print(f"  {d['_id']}: {d['n']}")

if DRY_RUN:
    print("\n[DRY_RUN] no updates. Use --apply or DRY_RUN=0 to apply.")
else:
    r = db.participants.update_many(FILTER, UPDATE)
    print(f"\nupdated: {r.modified_count}")

c.close()
