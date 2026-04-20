"""
관리자 계정 추가/갱신 (관리자 토큰 자동 생성).

Usage:
    python scripts/add_admin.py \
        --email blaster@auri.re.kr --name "조상규" --role "owner"
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient
from services.token_service import generate_admin_token


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--role", default="manager", choices=["owner", "manager", "viewer"])
    ap.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    ap.add_argument("--secret", default=os.getenv("ADMIN_TOKEN_SECRET", "change-me-admin-secret"))
    ap.add_argument("--admin-base-url", default=os.getenv("ADMIN_BASE_URL", "https://burn001.github.io/auri-ai-survey/admin"))
    args = ap.parse_args()

    email = args.email.strip().lower()
    token = generate_admin_token(email, args.secret)

    doc = {
        "email": email,
        "name": args.name.strip(),
        "role": args.role,
        "token": token,
        "active": True,
        "created_at": datetime.now(timezone.utc),
    }

    client = MongoClient(args.uri)
    db = client[args.db]
    res = db.admins.update_one({"email": email}, {"$set": doc}, upsert=True)
    action = "inserted" if res.upserted_id else "updated"
    print(f"{action}: {args.name} | {email} | role={args.role} | token={token}")
    print(f"admin URL: {args.admin_base_url}/?token={token}")
    client.close()


if __name__ == "__main__":
    main()
