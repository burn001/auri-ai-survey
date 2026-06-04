"""CATEGORY_TO_Q6_INDEX ReferenceError 운영 영향 진단.

5/20 09:46 KST(0046 UTC) 이후 — frontend 908027c 배포 시점 — 신규 응답이 실제로
얼마나 차단되었는지 추정.

지표:
  A) started_at 분포 — 정원 게이트 통과(=/start 200) 카운트
  B) submitted_at 분포 — 실제 응답 완료 카운트
  C) A−B = 본문 진입 실패(또는 진행 중) 카운트
  D) email_logs.completion = 자동 완료 메일 발사 카운트
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from services.db import connect, get_db


KST = timezone(timedelta(hours=9))
HOTFIX_BOUNDARY = datetime(2026, 5, 20, 0, 46)  # UTC = 09:46 KST


def fmt(dt):
    if not dt:
        return "-"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%m/%d %H:%M")
    return str(dt)


async def main():
    await connect()
    d = get_db()

    # 전체 응답 / 본 분석 (source != staff)
    total_resp = await d.responses.count_documents({"submitted_at": {"$ne": None}})
    analy_resp_pipeline = [
        {"$match": {"submitted_at": {"$ne": None}}},
        {"$lookup": {"from": "participants", "localField": "token", "foreignField": "token", "as": "p"}},
        {"$unwind": "$p"},
        {"$match": {"p.source": {"$ne": "staff"}}},
        {"$count": "n"},
    ]
    n = 0
    async for x in d.responses.aggregate(analy_resp_pipeline):
        n = x["n"]
    print(f"=== 응답 누계 ===")
    print(f"  total submitted: {total_resp}")
    print(f"  본 분석(staff 제외): {n}")

    # 시간대별 응답 — 5/19 0시 ~ 현재
    since = datetime(2026, 5, 19, 0, 0)
    pipeline = [
        {"$match": {"submitted_at": {"$gte": since}}},
        {"$lookup": {"from": "participants", "localField": "token", "foreignField": "token", "as": "p"}},
        {"$unwind": "$p"},
        {"$match": {"p.source": {"$ne": "staff"}}},
        {"$project": {"_id": 0, "submitted_at": 1, "name": "$p.name", "category": "$p.category", "email": "$p.email"}},
        {"$sort": {"submitted_at": 1}},
    ]
    print(f"\n=== 5/19 이후 신규 응답 (KST) — 본 분석만 ===")
    print(f"  hotfix boundary: {fmt(HOTFIX_BOUNDARY)} KST (frontend 908027c 배포)")
    rows = []
    async for r in d.responses.aggregate(pipeline):
        rows.append(r)
    if not rows:
        print("  (응답 없음)")
    for r in rows:
        ts = r["submitted_at"]
        boundary_ind = "🔴" if ts >= HOTFIX_BOUNDARY else "  "
        print(f"  {boundary_ind} {fmt(ts)} {r.get('category','-'):<8} {r.get('name','-'):<10} {r.get('email','-')}")

    before = sum(1 for r in rows if r["submitted_at"] < HOTFIX_BOUNDARY)
    after = sum(1 for r in rows if r["submitted_at"] >= HOTFIX_BOUNDARY)
    print(f"\n  hotfix 이전(5/19 ~ 5/20 09:46 KST): {before}건")
    print(f"  hotfix 이후(5/20 09:46 KST 이후):    {after}건")

    # 시작했지만 미제출 (started_at 박혔는데 submitted_at=null)
    pipeline_started = [
        {"$match": {"started_at": {"$gte": since}, "source": {"$ne": "staff"}}},
        {"$lookup": {"from": "responses", "localField": "token", "foreignField": "token", "as": "r"}},
        {"$match": {"$or": [{"r": {"$size": 0}}, {"r.submitted_at": None}]}},
        {"$project": {"_id": 0, "started_at": 1, "name": 1, "category": 1, "email": 1}},
        {"$sort": {"started_at": 1}},
    ]
    print(f"\n=== /start 통과했지만 미제출 (started_at 있음, submitted_at 없음, staff 제외) ===")
    started_rows = []
    async for r in d.participants.aggregate(pipeline_started):
        started_rows.append(r)
    if not started_rows:
        print("  (해당 없음)")
    after_started = 0
    before_started = 0
    for r in started_rows:
        ts = r["started_at"]
        boundary_ind = "🔴" if ts >= HOTFIX_BOUNDARY else "  "
        print(f"  {boundary_ind} {fmt(ts)} {r.get('category','-'):<8} {r.get('name','-'):<10} {r.get('email','-')}")
        if ts >= HOTFIX_BOUNDARY:
            after_started += 1
        else:
            before_started += 1
    print(f"\n  hotfix 이전 started-but-not-submitted: {before_started}건")
    print(f"  hotfix 이후 started-but-not-submitted: {after_started}건  ← 버그 영향 추정")


if __name__ == "__main__":
    asyncio.run(main())
