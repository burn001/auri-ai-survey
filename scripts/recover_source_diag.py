"""참가자 source 복구 진단 (1) — 실수로 to_staff_cleanup 된 토큰의 원래 source 추정 보조.

같은 소속(org) 응답자들의 source 분포를 보고, 잘못 덮인 source 원래 값을 추정한다.
읽기 전용 — DB 변경 없음. 실제 복구는 recover_source_apply.py (--apply 가드).

사용: docker exec auri-survey-api python /app/scripts/recover_source_diag.py <token> [--org <소속명>]
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from services.db import connect, get_db


async def main():
    if len(sys.argv) < 2:
        print("usage: recover_source_diag.py <token> [--org <org_name>]")
        return
    token = sys.argv[1]
    org = None
    if "--org" in sys.argv:
        i = sys.argv.index("--org")
        if i + 1 < len(sys.argv):
            org = sys.argv[i + 1]

    await connect()
    d = get_db()

    p = await d.participants.find_one({"token": token})
    if not p:
        print(f"participant not found for token {token}")
        return

    org = org or p.get("org")
    if org:
        print(f"=== 소속 '{org}' 응답자 source 분포 (추정 보조 자료) ===")
        async for r in d.participants.aggregate([
            {"$match": {"org": org}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]):
            print(f"  source='{r['_id']}': {r['n']}명")

    print(f"\n=== 현재 대상 doc ===")
    for k in ["token", "name", "email", "org", "category", "source", "consent_reward", "reward_phone", "started_at", "email_sent", "email_release_phase"]:
        print(f"  {k}: {p.get(k, '(없음)')}")


if __name__ == "__main__":
    asyncio.run(main())
