"""특정 토큰의 참가자·응답·메일 발송 상태 점검.

개별 안내 메일(hotfix 재시도 등) 발송 후 재진입·응답 여부 확인에 사용.

사용: docker exec auri-survey-api python /app/scripts/check_token_status.py <token>
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from services.db import connect, get_db

KST = timezone(timedelta(hours=9))


def fmt(dt):
    if not dt:
        return "-"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%m/%d %H:%M:%S")
    return str(dt)


async def main():
    if len(sys.argv) < 2:
        print("usage: check_token_status.py <token>")
        return
    token = sys.argv[1]

    await connect()
    d = get_db()

    p = await d.participants.find_one({"token": token})
    if not p:
        print(f"participant not found for token {token}")
        return
    print(f"=== participant ({token}) ===")
    for k in ["name", "email", "category", "source", "consent_reward", "consent_reward_at", "reward_phone", "started_at"]:
        v = p.get(k)
        if isinstance(v, datetime):
            v = fmt(v) + " KST"
        print(f"  {k}: {v}")

    r = await d.responses.find_one({"token": token})
    print(f"\n=== response ===")
    if not r:
        print("  (none)")
    else:
        for k in ["submitted_at", "updated_at", "quota_blocked", "quota_waived"]:
            v = r.get(k)
            if isinstance(v, datetime):
                v = fmt(v) + " KST"
            print(f"  {k}: {v}")
        print(f"  responses keys: {len(r.get('responses', {}))}")

    print(f"\n=== email_logs for token ===")
    async for log in d.email_logs.find(
        {"token": token}, {"_id": 0, "type": 1, "status": 1, "sent_at": 1}
    ).sort("sent_at", 1):
        ts = log.get("sent_at")
        if isinstance(ts, datetime):
            ts = fmt(ts) + " KST"
        print(f"  {ts}  {log.get('type'):<25} {log.get('status')}")


if __name__ == "__main__":
    asyncio.run(main())
