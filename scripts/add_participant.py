"""
단일 참가자 DB 추가/갱신 (토큰 자동 생성).

Usage:
    python scripts/add_participant.py \
        --name "조상규" --org "건축공간연구원" \
        --category "연구진" --email blaster@auri.re.kr
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient
from services.token_service import generate_token


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--org", required=True)
    ap.add_argument("--category", default="")
    ap.add_argument("--field", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", required=True)
    ap.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    ap.add_argument("--secret", default=os.getenv("TOKEN_SECRET", "change-me-in-production"))
    args = ap.parse_args()

    email = args.email.strip().lower()
    token = generate_token(email, args.secret)
    doc = {
        "name": args.name.strip(),
        "org": args.org.strip(),
        "category": args.category.strip(),
        "field": args.field.strip(),
        "phone": args.phone.strip(),
        "email": email,
        "token": token,
        "created_at": datetime.now(timezone.utc),
    }

    client = MongoClient(args.uri)
    db = client[args.db]
    res = db.participants.update_one({"email": email}, {"$set": doc}, upsert=True)
    action = "inserted" if res.upserted_id else "updated"
    print(f"{action}: {args.name} | {email} | token={token}")
    client.close()


if __name__ == "__main__":
    main()
