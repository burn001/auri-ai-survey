"""
기존 responses[*].comments ({qid: str}) → review_comments 컬렉션으로 마이그레이션.

기존 구조: 한 응답자(연구진)가 문항별로 단일 메모를 남김 (덮어쓰기 방식).
신규 구조: 모든 검토 코멘트를 review_comments 컬렉션에 entry 단위로 적재.
           survey_version 단위로 모든 연구진/관리자가 공유.

이 스크립트는 idempotent하지 않다 — 한 번만 실행할 것. 중복 적재가 우려되면
--dry-run으로 먼저 검증한 뒤 한 번만 실행.

Usage:
    python scripts/migrate_comments.py --dry-run
    python scripts/migrate_comments.py
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    ap.add_argument("--survey-version", default="v7")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = MongoClient(args.uri)
    db = client[args.db]

    print(f"[migrate_comments] DB={args.db} survey_version={args.survey_version} dry_run={args.dry_run}")

    existing = db.review_comments.count_documents({"survey_version": args.survey_version})
    print(f"  기존 review_comments(survey_version={args.survey_version}): {existing}건")

    cursor = db.responses.find(
        {"comments": {"$exists": True, "$ne": {}}},
        {"token": 1, "comments": 1, "survey_version": 1, "comments_updated_at": 1, "submitted_at": 1, "updated_at": 1},
    )

    to_insert = []
    seen_pairs: set[tuple[str, str]] = set()
    skip_dup = 0
    skip_empty = 0

    for resp in cursor:
        token = resp.get("token")
        legacy = resp.get("comments") or {}
        if not legacy:
            continue

        participant = db.participants.find_one(
            {"token": token},
            {"name": 1, "email": 1, "org": 1, "category": 1},
        )
        if not participant:
            print(f"  ! 참가자 없음: token={token} → 스킵")
            continue
        if participant.get("category") != "연구진":
            print(f"  ! 비연구진 응답에 코멘트가 있음: token={token} category={participant.get('category')} → 그대로 변환 (role=reviewer)")

        ts = (
            resp.get("comments_updated_at")
            or resp.get("updated_at")
            or resp.get("submitted_at")
            or datetime.now(timezone.utc).replace(tzinfo=None)
        )

        for qid, text in legacy.items():
            if not isinstance(text, str):
                continue
            t = text.strip()
            if not t:
                skip_empty += 1
                continue

            key = (token, qid)
            if key in seen_pairs:
                skip_dup += 1
                continue
            seen_pairs.add(key)

            already = db.review_comments.find_one({
                "survey_version": args.survey_version,
                "qid": qid,
                "author_token": token,
                "text": t,
            })
            if already:
                skip_dup += 1
                continue

            to_insert.append({
                "id": uuid4().hex,
                "survey_version": args.survey_version,
                "qid": qid,
                "author_role": "reviewer",
                "author_token": token,
                "author_name": participant.get("name", ""),
                "author_email": participant.get("email", ""),
                "author_org": participant.get("org", ""),
                "text": t,
                "status": "open",
                "parent_id": None,
                "created_at": ts,
                "updated_at": None,
                "status_changed_at": None,
                "status_changed_by": "",
                "_migrated_from_legacy": True,
            })

    print(f"  변환 대상: {len(to_insert)}건 (중복 스킵 {skip_dup}, 빈값 스킵 {skip_empty})")

    if not to_insert:
        print("  변환할 코멘트가 없습니다.")
        return

    if args.dry_run:
        print("  --dry-run 모드: 실제 적재하지 않음. 샘플 3건 ↓")
        for s in to_insert[:3]:
            print(f"    qid={s['qid']} author={s['author_name']} text={s['text'][:60]}...")
        return

    result = db.review_comments.insert_many(to_insert)
    print(f"  ✅ {len(result.inserted_ids)}건 적재 완료")


if __name__ == "__main__":
    main()
