"""참가자 source 복구 진단 (2) — 발송 phase·직군별 source 분포 확인.

복구 대상의 원래 source 후보를 좁히기 위해, 같은 발송 phase 및 같은 직군(category)
응답자들의 source 분포를 본다. 읽기 전용 — DB 변경 없음.

사용: docker exec auri-survey-api python /app/scripts/recover_source_diag2.py [--phase <phase>] [--category <직군>]
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from services.db import connect, get_db


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


async def main():
    phase = _arg("--phase")
    category = _arg("--category")

    await connect()
    d = get_db()

    if phase:
        print(f"=== release phase '{phase}' source 분포 ===")
        async for r in d.participants.aggregate([
            {"$match": {"email_release_phase": phase}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]):
            print(f"  source='{r['_id']}': {r['n']}명")

    if category:
        print(f"\n=== '{category}' 직군 source 분포 (전체) ===")
        async for r in d.participants.aggregate([
            {"$match": {"category": category}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]):
            print(f"  source='{r['_id']}': {r['n']}명")

    if not phase and not category:
        print("phase·category 둘 다 미지정 — 최소 하나는 필요")


if __name__ == "__main__":
    asyncio.run(main())
