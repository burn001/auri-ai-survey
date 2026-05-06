"""기존 응답에 대한 completion_email_states 백필 (idempotent).

각 submitted 응답 토큰에 대해:
1. 연구진 카테고리 → status='skipped'
2. email_logs 에 type='completion' 기록이 있으면 가장 최근 status 사용 (sent / failed)
3. 기록이 없으면 status='pending' (응답은 제출됐는데 메일 시도 흔적이 없는 경우)

이미 completion_email_states 에 token row 가 있으면 갱신하지 않음 (idempotent).
운영 중 backend 가 새 응답에 대해 자동으로 row 생성하므로, 마이그레이션은 1회만 필요.

사용:
  docker exec auri-survey-api python /tmp/migrate_completion.py [--dry-run] [--reset]

  --reset: 기존 completion_email_states 비우고 재구성 (개발용)
"""
import argparse
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, '/app')

from services.db import connect, disconnect, get_db


async def main(dry_run: bool, reset: bool) -> int:
    await connect()
    db = get_db()

    if reset:
        if dry_run:
            print("[dry] would clear completion_email_states")
        else:
            r = await db.completion_email_states.delete_many({})
            print(f"reset: cleared {r.deleted_count} existing rows")

    # 이미 row 있는 토큰 — 건너뛰기
    existing = set(await db.completion_email_states.distinct("token"))
    print(f"기존 completion_email_states 토큰: {len(existing)}")

    cursor = db.responses.find(
        {"submitted_at": {"$ne": None}}, {"_id": 0, "token": 1, "submitted_at": 1}
    )
    submitted = await cursor.to_list(length=100000)
    print(f"submitted 응답: {len(submitted)}")

    counts = {"skipped": 0, "sent": 0, "failed": 0, "pending": 0, "skipped_existing": 0, "no_participant": 0}
    for r in submitted:
        token = r.get("token")
        if not token:
            continue
        if token in existing:
            counts["skipped_existing"] += 1
            continue

        participant = await db.participants.find_one(
            {"token": token},
            {"_id": 0, "email": 1, "name": 1, "org": 1, "category": 1},
        )
        if not participant:
            counts["no_participant"] += 1
            continue

        category = participant.get("category", "")
        # 연구진 → skipped
        if category == "연구진":
            status = "skipped"
            counts["skipped"] += 1
            sent_at = None
            attempt_count = 0
            first_at = None
            last_at = None
            last_error = ""
        else:
            # email_logs 에서 가장 최근 completion 시도 조회
            latest = await db.email_logs.find_one(
                {"token": token, "type": "completion"},
                {"_id": 0, "status": 1, "sent_at": 1, "error": 1},
                sort=[("sent_at", -1)],
            )
            attempt_count = await db.email_logs.count_documents(
                {"token": token, "type": "completion"}
            )
            if not latest:
                status = "pending"
                counts["pending"] += 1
                sent_at = None
                first_at = None
                last_at = None
                last_error = ""
            else:
                status = latest.get("status", "pending")
                if status == "sent":
                    sent_at = latest.get("sent_at")
                    counts["sent"] += 1
                else:
                    sent_at = None
                    counts["failed"] += 1
                # first / last attempted at
                first_log = await db.email_logs.find_one(
                    {"token": token, "type": "completion"},
                    {"_id": 0, "sent_at": 1},
                    sort=[("sent_at", 1)],
                )
                first_at = first_log.get("sent_at") if first_log else latest.get("sent_at")
                last_at = latest.get("sent_at")
                last_error = latest.get("error", "") or ""

        doc = {
            "token": token,
            "email": participant.get("email", ""),
            "name": participant.get("name", ""),
            "org": participant.get("org", ""),
            "category": category,
            "status": status,
            "attempt_count": attempt_count,
            "first_attempted_at": first_at,
            "last_attempted_at": last_at,
            "sent_at": sent_at,
            "last_error": last_error,
            "is_resend": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "migrated": True,
        }

        if dry_run:
            print(f"[dry] {token} status={status} category={category} attempts={attempt_count}")
            continue

        await db.completion_email_states.insert_one(doc)

    print("\n=== summary ===")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    # 사후 검증
    if not dry_run:
        total = await db.completion_email_states.count_documents({})
        by_status = {}
        for s in ["sent", "failed", "pending", "skipped"]:
            by_status[s] = await db.completion_email_states.count_documents({"status": s})
        print(f"\ncompletion_email_states 총 {total}건")
        for k, v in by_status.items():
            print(f"  {k}: {v}")

    await disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="기존 completion_email_states 비우기")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.reset)))
