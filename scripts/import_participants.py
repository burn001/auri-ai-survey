"""
설문조사 대상자 통합 xlsx → MongoDB 일괄 등록 + 토큰 생성

Usage:
    python scripts/import_participants.py <xlsx_path> [--uri MONGODB_URI] [--secret TOKEN_SECRET]

Example:
    python scripts/import_participants.py "../설문조사 풀/설문조사 대상자 통합 (2026.04.16).xlsx"
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from pymongo import MongoClient, UpdateOne
from services.token_service import generate_token


def load_participants(xlsx_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    records = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        # 번호, 이름, 소속, 직군분류, 전문분야(기존), 연락처, 이메일, 출처
        name = row[1]
        if not name or str(name).strip() == "":
            continue
        email = str(row[6]).strip().lower() if row[6] else ""
        if not email or email in ("none", ""):
            continue

        records.append({
            "name": str(name).strip(),
            "org": str(row[2]).strip() if row[2] else "",
            "category": str(row[3]).strip() if row[3] else "",
            "field": str(row[4]).strip() if row[4] and str(row[4]).strip() not in ("None", "") else "",
            "phone": str(row[5]).strip() if row[5] else "",
            "email": email,
        })

    wb.close()
    return records


def main():
    parser = argparse.ArgumentParser(description="Import survey participants to MongoDB")
    parser.add_argument("xlsx", help="Path to consolidated participants xlsx")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    parser.add_argument("--secret", default=os.getenv("TOKEN_SECRET", "change-me-in-production"))
    parser.add_argument("--dry-run", action="store_true", help="Print without inserting")
    args = parser.parse_args()

    print(f"Loading: {args.xlsx}")
    records = load_participants(args.xlsx)
    print(f"Loaded {len(records)} participants with email")

    for r in records:
        r["token"] = generate_token(r["email"], args.secret)

    # Check for token collisions
    tokens = [r["token"] for r in records]
    if len(tokens) != len(set(tokens)):
        dupes = [t for t in tokens if tokens.count(t) > 1]
        print(f"WARNING: {len(set(dupes))} token collisions detected")

    if args.dry_run:
        print("\n--- DRY RUN (first 10) ---")
        for r in records[:10]:
            print(f"  {r['token']} | {r['name']} | {r['email']} | {r['category']}")
        print(f"\nTotal: {len(records)} (would be upserted)")
        return

    print(f"Connecting to {args.uri} / {args.db}")
    client = MongoClient(args.uri)
    db = client[args.db]

    db.participants.create_index("token", unique=True)
    db.participants.create_index("email", unique=True)

    operations = []
    for r in records:
        from datetime import datetime, timezone
        r["created_at"] = datetime.now(timezone.utc)
        operations.append(
            UpdateOne(
                {"email": r["email"]},
                {"$set": r},
                upsert=True,
            )
        )

    result = db.participants.bulk_write(operations)
    print(f"Done: {result.upserted_count} inserted, {result.modified_count} updated, {result.matched_count} matched")

    total = db.participants.count_documents({})
    print(f"Total participants in DB: {total}")

    client.close()


if __name__ == "__main__":
    main()
