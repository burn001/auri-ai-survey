"""Phase 2A — 명함·유지관리협회 334건을 발송 풀로 풀어줌.

대상 source:
- "대상자 추가(명함)"  (223건, 이름·휴대폰 보유 양호)
- "유지관리협회 회원사" (111건)

변경:
- email_sent: True → False
- email_skip_reason: "bulk_import_2026_05_04_pending_review" → "" (unset 대신 빈 문자열)
- email_released_at: <now> 신규 필드로 풀린 시점 기록

DRY_RUN 토글 — 인자 없이 실행하면 dry-run, --apply 인자 시 실제 적용.
"""
import sys, re
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient

URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true"
DB = "auri_survey"
TARGET_SOURCES = ["대상자 추가(명함)", "유지관리협회 회원사"]
SKIP_REASON = "bulk_import_2026_05_04_pending_review"
APPLY = "--apply" in sys.argv

c = MongoClient(URI)
db = c[DB]

query = {
    "source": {"$in": TARGET_SOURCES},
    "email_skip_reason": SKIP_REASON,
    "email_sent": True,
}

count = db.participants.count_documents(query)
print(f"=== Phase 2A 풀어주기 (DRY_RUN={'OFF (실제 적용)' if APPLY else 'ON'}) ===")
print(f"대상 source: {TARGET_SOURCES}")
print(f"매칭 doc: {count}")

# 풀어주기 전 내역 확인
by_source = {}
by_category = {}
no_phone = 0
for d in db.participants.find(query, {"source": 1, "category": 1, "phone_normalized": 1}):
    by_source[d.get("source")] = by_source.get(d.get("source"), 0) + 1
    by_category[d.get("category")] = by_category.get(d.get("category"), 0) + 1
    if not d.get("phone_normalized"):
        no_phone += 1

print(f"\n source별: {by_source}")
print(f" category별: {by_category}")
print(f" 휴대폰 정보 없는 doc: {no_phone}")

# 활성 풀 before
before = db.participants.count_documents({
    "source": {"$ne": "staff"},
    "category": {"$in": ["설계", "시공", "유지관리", "건축행정"]},
    "email_sent": {"$ne": True}
})
print(f"\n변경 전 활성 발송 풀: {before}")

if not APPLY:
    print(f"\nDRY_RUN — 실제 변경 없음. 적용하려면 --apply 인자 추가.")
    print(f"예상 결과: 활성 풀 {before} → {before + count} (+{count})")
    c.close()
    sys.exit(0)

# 실제 적용
now = datetime.now(timezone.utc)
result = db.participants.update_many(
    query,
    {
        "$set": {
            "email_sent": False,
            "email_skip_reason": "",
            "email_released_at": now,
            "email_release_phase": "phase2a_2026_05_04",
            "updated_at": now,
        }
    }
)
print(f"\n적용 결과: matched={result.matched_count}, modified={result.modified_count}")

# 활성 풀 after
after = db.participants.count_documents({
    "source": {"$ne": "staff"},
    "category": {"$in": ["설계", "시공", "유지관리", "건축행정"]},
    "email_sent": {"$ne": True}
})
print(f"변경 후 활성 발송 풀: {after} (delta {after - before})")

still_flagged = db.participants.count_documents({"email_skip_reason": SKIP_REASON})
print(f"여전히 skip-flagged: {still_flagged} (5,321 - {count} = {5321 - count} 예상)")

c.close()
