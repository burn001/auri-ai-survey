"""
v3 풀 → MongoDB 신규 import (옵션 C 전체).

기존 `import_participants.py`와의 차이:
- CSV 입력 (정제된 후보 5,321건)
- 이름 결측 허용 (placeholder "" — 응답 시 reward_name으로 회수)
- `source` 필드를 v3 출처값으로 채움
- 신규 행에 자동 발송 차단 마킹 (`email_sent=true`, `email_skip_reason`, `bulk_imported_at`)
- 기존 doc 절대 건드리지 않음: `$setOnInsert`만 사용 → 운영 메타데이터(email_sent_at, bounced 등) 보호

사용:
    python scripts/import_v4_pool.py <csv_path> [--dry-run]

환경:
    MONGODB_URI / MONGODB_DB / TOKEN_SECRET
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient, UpdateOne
from services.token_service import generate_token


SKIP_REASON = "bulk_import_2026_05_04_pending_review"


def load_csv(csv_path: str) -> list[dict]:
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            email = (r.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            rows.append({
                "name": (r.get("name") or "").strip(),
                "org": (r.get("org") or "").strip(),
                "category": (r.get("category") or "").strip(),
                "field": (r.get("field") or "").strip(),
                "phone": (r.get("phone") or "").strip(),
                "email": email,
                "source": (r.get("source") or "").strip(),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description="v4 pool import to MongoDB")
    parser.add_argument("csv", help="Path to import_v4_candidates.csv")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    parser.add_argument("--secret", default=os.getenv("TOKEN_SECRET", "change-me-in-production"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.secret == "change-me-in-production":
        print("ERROR: TOKEN_SECRET 환경변수를 설정하세요 (production secret 필요)", file=sys.stderr)
        sys.exit(1)

    print(f"Loading: {args.csv}")
    records = load_csv(args.csv)
    print(f"Loaded {len(records)} records (이메일 보유)")

    now = datetime.now(timezone.utc)
    for r in records:
        r["token"] = generate_token(r["email"], args.secret)
        r["created_at"] = now
        r["bulk_imported_at"] = now
        r["email_sent"] = True
        r["email_skip_reason"] = SKIP_REASON

    tokens = [r["token"] for r in records]
    if len(tokens) != len(set(tokens)):
        from collections import Counter
        c = Counter(tokens)
        dupes = [t for t, n in c.items() if n > 1]
        print(f"WARNING: token 충돌 {len(dupes)}건 — production secret 점검 필요")
        sys.exit(2)

    print(f"\nMongoDB 연결: {args.uri} / {args.db}")
    client = MongoClient(args.uri)
    db = client[args.db]

    pre_total = db.participants.count_documents({})
    pre_existing_emails = set(db.participants.distinct("email"))
    new_emails = {r["email"] for r in records}
    overlap = new_emails & pre_existing_emails
    print(f"  기존 participants: {pre_total}")
    print(f"  CSV 이메일: {len(new_emails)} (unique)")
    print(f"  기존과 이메일 중복: {len(overlap)}  ← 이들은 $setOnInsert로 무변경")

    if args.dry_run:
        print("\n=== DRY RUN 샘플 (first 5) ===")
        for r in records[:5]:
            print(f"  {r['token']} | {r['email']:35s} | {r['name'] or '(no name)':10s} | "
                  f"category={r['category']:>4s} | source={r['source']}")
        print(f"\n예상: 신규 insert {len(records) - len(overlap)} / 매칭 {len(overlap)} (변경 없음)")
        print(f"DB 내 총합 예상: {pre_total + len(records) - len(overlap)}")
        client.close()
        return

    # 안전 인덱스 (이미 있으면 무해)
    db.participants.create_index("token", unique=True)
    db.participants.create_index("email", unique=True)

    operations = [
        UpdateOne(
            {"email": r["email"]},
            {"$setOnInsert": r},   # ← 핵심: 기존 doc은 어떤 필드도 변경하지 않음
            upsert=True,
        )
        for r in records
    ]
    result = db.participants.bulk_write(operations, ordered=False)
    post_total = db.participants.count_documents({})
    print(f"\n=== 결과 ===")
    print(f"  upserted: {result.upserted_count}")
    print(f"  matched (변경 없음): {result.matched_count}")
    print(f"  modified: {result.modified_count} (← 0이어야 함, $setOnInsert)")
    print(f"  DB total: {pre_total} → {post_total} (delta {post_total - pre_total})")

    suppressed = db.participants.count_documents({"email_skip_reason": SKIP_REASON})
    print(f"  skip-flagged docs (자동 발송 차단): {suppressed}")

    client.close()


if __name__ == "__main__":
    main()
