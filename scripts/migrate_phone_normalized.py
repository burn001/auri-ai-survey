"""기존 participant doc 의 phone 을 phone_normalized 로 마이그레이션.

phone 필드가 있고 phone_normalized 가 없는 doc 만 대상.
멱등 — 여러 번 실행해도 안전.
"""
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient

URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true"
DB = "auri_survey"

def normalize(p: str) -> str:
    if not p:
        return ""
    return re.sub(r"\D+", "", p)

c = MongoClient(URI)
db = c[DB]

# phone_normalized 가 없는 doc 만 대상
cursor = db.participants.find(
    {"phone_normalized": {"$exists": False}},
    {"_id": 1, "phone": 1, "reward_phone": 1, "consent_reward": 1, "name": 1}
)

updated = 0
filled = 0
empty = 0
for d in cursor:
    # 우선순위: phone > consent_reward 시 reward_phone
    raw = d.get("phone", "") or ""
    if not raw and d.get("consent_reward"):
        raw = d.get("reward_phone", "") or ""
    norm = normalize(raw)
    db.participants.update_one(
        {"_id": d["_id"]},
        {"$set": {"phone_normalized": norm}}
    )
    updated += 1
    if norm:
        filled += 1
    else:
        empty += 1

print(f"phone_normalized 마이그레이션 완료")
print(f"  처리 doc: {updated}")
print(f"  값 있는 doc: {filled}")
print(f"  값 없는 doc (빈 문자열): {empty}")

# 최종 검증
total = db.participants.count_documents({})
has_norm = db.participants.count_documents({"phone_normalized": {"$exists": True}})
print(f"  전체 participants: {total}")
print(f"  phone_normalized 보유: {has_norm}")

# (name, phone_normalized) 중복 검출 — 이미 시스템에 잠재적 중복 유무 확인
pipeline = [
    {"$match": {"name": {"$ne": ""}, "phone_normalized": {"$ne": ""}}},
    {"$group": {"_id": {"name": "$name", "phone": "$phone_normalized"}, "count": {"$sum": 1}, "tokens": {"$push": "$token"}}},
    {"$match": {"count": {"$gt": 1}}}
]
dups = list(db.participants.aggregate(pipeline))
print(f"\n잠재 중복 (이름+휴대폰 동일, 토큰만 다름): {len(dups)} 그룹")
for d in dups[:10]:
    print(f"  {d['_id']['name']} / {d['_id']['phone']} → tokens: {d['tokens']}")

c.close()
