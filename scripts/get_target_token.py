"""이메일로 참가자 토큰 조회 — 개별 안내 메일(hotfix 영향 등) 발송 대상 식별용.

사용: docker exec auri-survey-api python /app/scripts/get_target_token.py <email>
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from services.db import connect, get_db


async def main():
    if len(sys.argv) < 2:
        print("usage: get_target_token.py <email>")
        return
    email = sys.argv[1]
    await connect()
    d = get_db()
    p = await d.participants.find_one(
        {"email": email},
        {"_id": 0, "token": 1, "name": 1, "email": 1, "org": 1, "category": 1, "started_at": 1},
    )
    print(p if p else "NOT FOUND")


if __name__ == "__main__":
    asyncio.run(main())
