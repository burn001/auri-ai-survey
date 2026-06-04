"""fill_missing_notice 발송 결과 + 보완 응답자 카운트 + 상세 진단.

실행: docker exec auri-survey-api python /tmp/check_fill_missing_status.py
"""
import asyncio
import json
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from services.db import connect, disconnect, get_db


async def main():
    await connect()
    d = get_db()

    # 발송 결과
    sent = await d.email_logs.count_documents({"type": "fill_missing_notice", "status": "sent"})
    failed = await d.email_logs.count_documents({"type": "fill_missing_notice", "status": "failed"})
    total = await d.email_logs.count_documents({"type": "fill_missing_notice"})

    # 보완 응답자 (fill_missing_log 필드 존재 → 최소 1회 보완 endpoint 호출)
    filled = await d.responses.count_documents({"fill_missing_log": {"$exists": True}})

    print(f"=== fill_missing_notice 발송 결과 ===")
    print(f"sent:   {sent}")
    print(f"failed: {failed}")
    print(f"total:  {total}")
    print()
    print(f"=== 보완 응답자 ===")
    print(f"fill_missing_log 보유 응답 doc: {filled}")
    print()

    # 발송 상세 (시각·이메일·status)
    print(f"=== 발송 상세 (최근 35건) ===")
    async for log in d.email_logs.find(
        {"type": "fill_missing_notice"},
        {"_id": 0, "sent_at": 1, "email": 1, "name": 1, "status": 1, "error": 1, "token": 1},
    ).sort("sent_at", 1).limit(35):
        ts = log.get("sent_at")
        if isinstance(ts, datetime):
            ts = ts.strftime("%H:%M:%S")
        line = f"  [{ts}] {log.get('status'):<6} {log.get('email','-'):<40} {log.get('name','-')}"
        if log.get("error"):
            line += f" — {log['error'][:80]}"
        print(line)

    # 보완 응답 상세 (있다면)
    if filled > 0:
        print()
        print(f"=== 보완 응답 상세 ({filled}건) ===")
        async for resp in d.responses.find(
            {"fill_missing_log": {"$exists": True}},
            {"_id": 0, "token": 1, "fill_missing_log": 1},
        ).limit(35):
            token = resp.get("token", "")
            logs = resp.get("fill_missing_log", [])
            participant = await d.participants.find_one(
                {"token": token}, {"_id": 0, "name": 1, "email": 1, "org": 1}
            )
            name = participant.get("name", "?") if participant else "?"
            org = participant.get("org", "?") if participant else "?"
            for entry in logs:
                at = entry.get("at")
                if isinstance(at, datetime):
                    at = at.strftime("%H:%M:%S")
                qids = entry.get("filled_qids", [])
                print(f"  [{at}] {name} ({org}) — filled={qids}")


if __name__ == "__main__":
    asyncio.run(main())
