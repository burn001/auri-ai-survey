"""실패한 완료 메일 재발송 — completion_email_states 기반.

대상: completion_email_states.status ∈ ('failed', 'pending')
- 'pending' 은 응답 직후 시도가 실제 결과(sent/failed)로 갱신되지 않은 케이스 — drift 보강
- 'failed' 은 한도 초과 등으로 발송 실패한 케이스

backend 의 _send_completion_email 을 재사용해 SMTP 시도 + state 갱신을 일관 처리.
한도 초과 (5.4.5 / daily / limit exceeded) 재감지 시 즉시 abort.

사용:
  docker exec auri-survey-api python /tmp/resend.py [--dry-run] [--limit N] [--include-pending]
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, '/app')

from services.db import connect, disconnect, get_db
from routers.responses import _send_completion_email


async def main(dry_run: bool, limit: int, include_pending: bool) -> int:
    await connect()
    db = get_db()

    statuses = ["failed"]
    if include_pending:
        statuses.append("pending")

    cursor = db.completion_email_states.find(
        {"status": {"$in": statuses}},
        {"_id": 0, "token": 1, "email": 1, "category": 1, "status": 1, "attempt_count": 1, "last_error": 1},
    ).sort([("last_attempted_at", 1)])
    candidates = await cursor.to_list(length=20000)
    print(f"재시도 대상 (status ∈ {statuses}): {len(candidates)}")
    if limit and limit > 0:
        candidates = candidates[:limit]
        print(f"limit 적용: {len(candidates)}")

    sent_n = 0
    failed_again = []
    skipped = 0
    abort = False

    for i, c in enumerate(candidates, 1):
        token = c["token"]
        # 응답이 실제로 제출됐는지 안전망 검사
        resp = await db.responses.find_one(
            {"token": token, "submitted_at": {"$ne": None}}, {"_id": 1}
        )
        if not resp:
            print(f"[{i}/{len(candidates)}] {token} skip — 응답 미제출")
            skipped += 1
            continue

        participant = await db.participants.find_one({"token": token})
        if not participant or not participant.get("email"):
            print(f"[{i}/{len(candidates)}] {token} skip — participant/email 없음")
            skipped += 1
            continue

        if dry_run:
            print(f"[{i}/{len(candidates)}] [dry] {participant['email']} ({participant.get('category','')}) status={c['status']} attempts={c.get('attempt_count',0)}")
            continue

        # _send_completion_email 이 SMTP 시도 + email_logs 기록 + completion_email_states 갱신을 모두 처리.
        # 발송 자체는 성공/실패와 무관하게 raise 하지 않음.
        await _send_completion_email(participant, token, is_resend=True)

        # 결과 확인
        post = await db.completion_email_states.find_one({"token": token}, {"_id": 0, "status": 1, "last_error": 1})
        new_status = post.get("status") if post else "unknown"
        if new_status == "sent":
            print(f"[{i}/{len(candidates)}] sent {participant['email']}")
            sent_n += 1
            time.sleep(2)
        else:
            err = (post or {}).get("last_error", "")[:300]
            print(f"[{i}/{len(candidates)}] FAIL {participant['email']}: {err}")
            failed_again.append({"token": token, "email": participant["email"], "error": err})
            if "5.4.5" in err or "daily" in err.lower() or "limit exceeded" in err.lower():
                print("[ABORT] Gmail 한도 초과 — 한도 풀린 후 다시 실행하십시오.")
                abort = True
                break
            time.sleep(3)

    print(f"\n=== summary ===")
    print(f"sent={sent_n} skipped={skipped} failed_again={len(failed_again)} aborted={abort}")
    if failed_again:
        for f in failed_again[:3]:
            print(f"  - {f['email']}: {f['error'][:120]}")

    # 사후 카운트
    by_status = {}
    for s in ["sent", "failed", "pending", "skipped"]:
        by_status[s] = await db.completion_email_states.count_documents({"status": s})
    print(f"\ncompletion_email_states: {by_status}")

    await disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-pending", action="store_true", help="status='pending' 도 재시도 (drift 보강)")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.limit, args.include_pending)))
