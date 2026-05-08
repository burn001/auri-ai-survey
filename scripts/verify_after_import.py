import sys
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient

c = MongoClient("mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true")
db = c.auri_survey

total = db.participants.count_documents({})
flagged = db.participants.count_documents({"email_skip_reason": "bulk_import_2026_05_04_pending_review"})
dispatch = db.participants.count_documents({
    "source": {"$ne": "staff"},
    "category": {"$in": ["설계", "시공", "유지관리", "건축행정"]},
    "email_sent": {"$ne": True}
})

print("total participants:", total)
print("skip-flagged (bulk_import):", flagged)
print("활성 발송 풀(email_sent != true, 4직군, non-staff):", dispatch)
print()
print("=== source 분포 ===")
for d in db.participants.aggregate([
    {"$group": {"_id": "$source", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}}
]):
    print(" ", d["_id"] or "(none)", ":", d["count"])
print()
print("=== category 분포 ===")
for d in db.participants.aggregate([
    {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}}
]):
    print(" ", d["_id"] or "(none)", ":", d["count"])
print()
print("=== email_skip_reason 분포 ===")
for d in db.participants.aggregate([
    {"$match": {"email_skip_reason": {"$ne": None, "$ne": ""}}},
    {"$group": {"_id": "$email_skip_reason", "count": {"$sum": 1}}}
]):
    print(" ", d["_id"], ":", d["count"])

# 샘플 신규 doc 확인
print()
print("=== 신규 doc 샘플 5건 ===")
for d in db.participants.find({"email_skip_reason": "bulk_import_2026_05_04_pending_review"}).limit(5):
    print(f"  token={d['token']}, email={d['email']}, name={d.get('name','')!r}, source={d.get('source')}, "
          f"category={d.get('category')}, email_sent={d.get('email_sent')}, "
          f"bulk_imported_at={d.get('bulk_imported_at')}")
