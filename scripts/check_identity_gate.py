"""신원 게이트 동작 확인 — 익명 토큰 1개로 verify 응답 + 가짜 신원 입력으로 409 검증."""
import sys, json
import urllib.request, ssl
sys.stdout.reconfigure(encoding='utf-8')

API = "https://alris.ddns.net:8443/ai/api"
ctx = ssl.create_default_context()

# 1) 익명 토큰 1개를 DB에서 직접 찾아서 verify
import re
from pymongo import MongoClient
c = MongoClient("mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true")
db = c.auri_survey
anon = db.participants.find_one(
    {"email_skip_reason": "bulk_import_2026_05_04_pending_review", "name": ""},
    {"_id": 0, "token": 1, "email": 1, "name": 1, "source": 1}
)
print("anon 토큰 샘플:", anon)

token = anon["token"]

# 2) verify_token — needs_identity=true 여야 함
req = urllib.request.Request(f"{API}/survey/{token}", method="GET")
with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
    data = json.loads(r.read().decode())
print(f"\nverify needs_identity: {data.get('needs_identity')}")
print(f"verify name: '{data.get('name')}'")
assert data.get("needs_identity") is True, "needs_identity 가 true 가 아닙니다"

# 3) 가짜 신원 입력 — 1차 발송 응답자 중 한 명과 동일한 이름·휴대폰을 사용하면 409
# 먼저 phone_normalized 가 있는 doc 한 명 찾기
sample = db.participants.find_one(
    {"phone_normalized": {"$ne": ""}, "name": {"$ne": ""}, "email_skip_reason": {"$ne": "bulk_import_2026_05_04_pending_review"}},
    {"_id": 0, "name": 1, "phone": 1, "phone_normalized": 1}
)
print(f"\n중복 충돌 시뮬레이션 대상: {sample['name']} / {sample['phone']} (norm={sample['phone_normalized']})")

payload = json.dumps({
    "token": token,
    "name": sample["name"],
    "phone": sample["phone"],
    "org": "테스트소속"
}).encode()
req = urllib.request.Request(
    f"{API}/survey/identity",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        body = json.loads(r.read().decode())
    print(f"\n  ❌ 예상: 409 차단, 실제: {r.status}: {body}")
except urllib.error.HTTPError as e:
    err_body = e.read().decode()
    print(f"\n  ✅ {e.code}: {err_body[:200]}")
    if e.code != 409:
        print("  ⚠ 409 가 아닙니다 — 차단 로직 점검 필요")

# 4) DB doc 확인 — 신원 입력 X 상태 유지
post = db.participants.find_one({"token": token}, {"_id": 0, "name": 1, "phone_normalized": 1, "identity_filled_at": 1})
print(f"\n실패 후 doc: name='{post.get('name')}', phone_normalized='{post.get('phone_normalized', '')}', identity_filled_at={post.get('identity_filled_at')}")
print("  doc 무변경 확인:", post.get('name') == '' and not post.get('identity_filled_at'))

c.close()
