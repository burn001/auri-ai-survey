"""E2E S2·S3·S4 시나리오 트리거를 위한 토큰 source 일시 토글.

운영 영향 방지:
- staff → e2e_test 토글: _completed_count_by_category 카운트엔 들어가지만 응답 제출 안 함(quota 영향 없음)
- e2e_test → staff 복귀: quota_blocked_at·quota_waived·started_at·consent_reward 마킹 모두 unset

사용: docker exec auri-survey-api python /app/scripts/toggle_source.py <action> <token>[ <token> ...]
  action ∈ {to_e2e, to_staff_cleanup}
"""
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from services.db import connect, get_db


async def main():
    if len(sys.argv) < 3:
        print("usage: toggle_source.py <to_e2e|to_staff_cleanup> <token> [<token> ...]")
        sys.exit(2)
    action = sys.argv[1]
    tokens = sys.argv[2:]

    await connect()
    d = get_db()

    if action == "to_e2e":
        # staff → e2e_test: quota gate 트리거 가능 상태
        # SAFETY GUARD: 현재 source가 staff인 경우만 변경 (실 운영 토큰 보호)
        for tok in tokens:
            cur = await d.participants.find_one({"token": tok}, {"_id": 0, "source": 1, "email": 1})
            if not cur:
                print(f"  {tok}: SKIP (not found)")
                continue
            if cur.get("source") != "staff":
                print(f"  {tok}: SKIP — source='{cur.get('source')}' (staff 아닌 토큰. 운영 데이터 보호)")
                continue
            r = await d.participants.update_one(
                {"token": tok, "source": "staff"},
                {"$set": {"source": "e2e_test", "source_original": "staff"}},
            )
            print(f"  {tok} → e2e_test: matched={r.matched_count} modified={r.modified_count}")
    elif action == "to_staff_cleanup":
        # e2e_test → staff + 검증 흔적 cleanup (quota_blocked_at·quota_waived·started_at·consent_reward·responses partial)
        # SAFETY GUARD: source가 e2e_test인 토큰만 cleanup (실 운영 토큰 보호)
        for tok in tokens:
            cur = await d.participants.find_one({"token": tok}, {"_id": 0, "source": 1, "source_original": 1, "email": 1})
            if not cur:
                print(f"  {tok}: SKIP (not found)")
                continue
            if cur.get("source") != "e2e_test":
                print(f"  {tok}: SKIP — source='{cur.get('source')}' (e2e_test 아닌 토큰. 운영 데이터 보호)")
                continue
            r = await d.participants.update_one(
                {"token": tok, "source": "e2e_test"},
                {
                    "$set": {
                        "source": "staff",
                        "consent_reward": False,
                        "reward_phone": "",
                        "consent_reward_at": None,
                    },
                    "$unset": {
                        "source_original": "",
                        "quota_blocked_at": "",
                        "quota_blocked_category": "",
                        "quota_waived": "",
                        "quota_waived_category": "",
                        "quota_waived_at": "",
                        "started_at": "",
                    },
                },
            )
            # responses에 partial(submitted_at=null) 있으면 삭제
            del_r = await d.responses.delete_one({"token": tok, "submitted_at": None})
            print(f"  {tok} → staff: participant.modified={r.modified_count}, responses.deleted={del_r.deleted_count}")
    else:
        print(f"unknown action: {action}")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
