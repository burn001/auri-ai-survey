"""auri-ai-survey 분야별·출처별 응답 현황 집계.

응답(submitted_at != null) 기준으로 category·source별 발송수·응답수·응답률을 출력.

실행:
    # 컨테이너 내부
    docker exec -it auri-survey-api python /app/scripts/check_response_by_category.py

    # 로컬(SSH 터널 37017 가정)
    MONGODB_URI="mongodb://alrisAdmin:...@127.0.0.1:37017/?authSource=admin&directConnection=true" \
        python scripts/check_response_by_category.py

옵션:
    --by-source        출처(source)별 표 추가 출력
    --cross            category × source 교차표 출력
    --json             결과를 JSON으로도 출력 (telegram report 등에 활용)
"""
import argparse
import json
import os
import sys
from pymongo import MongoClient

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@127.0.0.1:27017/?authSource=admin&directConnection=true"

# 응답자 Q6 자기응답 인덱스 → 분야. backend Q6_INDEX_TO_CATEGORY와 동일.
Q6_INDEX_TO_CATEGORY = {0: "설계", 1: "시공", 2: "유지관리", 3: "건축행정"}


def by_category(db):
    """분야별 (풀, 발송, 응답).

    분모(풀·발송)는 participants.category(사전 발송 분류) 기준,
    분자(응답)는 responses.responses.Q6(응답자 자기응답) 기준으로 분리한다.
    Q6는 응답자만 가지므로 발송 분류와 그룹 기준이 다르며, 본 누계 보고의
    표준은 '분자만 Q6'다 (2026-05-26 정정 — 이전엔 분자도 발송분류였음).
    """
    # 분모: 발송 분류 기준 풀·발송
    send_rows = db.participants.aggregate([
        {"$group": {
            "_id": {"$ifNull": ["$category", "(미분류)"]},
            "pool": {"$sum": 1},
            "sent": {"$sum": {"$cond": ["$email_sent", 1, 0]}},
        }},
    ])
    agg = {r["_id"]: {"_id": r["_id"], "pool": r["pool"], "sent": r["sent"], "submitted": 0}
           for r in send_rows}

    # 분자: 제출 응답을 Q6 자기응답으로 그룹
    sub_rows = db.responses.aggregate([
        {"$match": {"submitted_at": {"$ne": None}}},
        {"$group": {"_id": "$responses.Q6", "n": {"$sum": 1}}},
    ])
    for r in sub_rows:
        key = r["_id"]
        try:
            key = int(key)
        except (TypeError, ValueError):
            pass
        label = Q6_INDEX_TO_CATEGORY.get(key, "(미분류)")
        if label not in agg:
            agg[label] = {"_id": label, "pool": 0, "sent": 0, "submitted": 0}
        agg[label]["submitted"] += r["n"]

    rows = list(agg.values())
    rows.sort(key=lambda x: x["submitted"], reverse=True)
    return rows


def by_source(db):
    pipeline = [
        {"$lookup": {"from": "responses", "localField": "token", "foreignField": "token", "as": "r"}},
        {"$project": {
            "source": {"$ifNull": ["$source", "(원래 풀)"]},
            "sent": {"$cond": ["$email_sent", 1, 0]},
            "submitted": {
                "$cond": [
                    {"$gt": [
                        {"$size": {"$filter": {"input": "$r", "cond": {"$ne": ["$$this.submitted_at", None]}}}},
                        0,
                    ]},
                    1, 0,
                ]
            },
        }},
        {"$group": {
            "_id": "$source",
            "pool": {"$sum": 1},
            "sent": {"$sum": "$sent"},
            "submitted": {"$sum": "$submitted"},
        }},
        {"$sort": {"sent": -1}},
    ]
    return list(db.participants.aggregate(pipeline))


def by_cross(db):
    pipeline = [
        {"$lookup": {"from": "responses", "localField": "token", "foreignField": "token", "as": "r"}},
        {"$match": {"r.submitted_at": {"$ne": None}}},
        {"$group": {
            "_id": {"category": {"$ifNull": ["$category", "(미분류)"]}, "source": {"$ifNull": ["$source", "(원래 풀)"]}},
            "submitted": {"$sum": 1},
        }},
        {"$sort": {"submitted": -1}},
    ]
    return list(db.participants.aggregate(pipeline))


def rate(num, denom):
    return round(num / denom * 100, 1) if denom else 0.0


def print_table(title, rows, key_label):
    print(f"\n=== {title} ===")
    print(f"  {key_label:<20s} {'풀':>6s} {'발송':>6s} {'응답':>6s} {'응답률(발송대비)':>16s}")
    print(f"  {'-' * 20} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 16}")
    tot_pool = tot_sent = tot_sub = 0
    for r in rows:
        label = str(r["_id"])[:20]
        pool, sent, sub = r["pool"], r["sent"], r["submitted"]
        tot_pool += pool
        tot_sent += sent
        tot_sub += sub
        print(f"  {label:<20s} {pool:>6d} {sent:>6d} {sub:>6d} {rate(sub, sent):>15.1f}%")
    print(f"  {'-' * 20} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 16}")
    print(f"  {'합계':<20s} {tot_pool:>6d} {tot_sent:>6d} {tot_sub:>6d} {rate(tot_sub, tot_sent):>15.1f}%")


def main():
    parser = argparse.ArgumentParser(description="ai-survey 분야별·출처별 응답 현황 집계")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", DEFAULT_URI))
    parser.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    parser.add_argument("--by-source", action="store_true", help="출처(source)별 표 추가 출력")
    parser.add_argument("--cross", action="store_true", help="category × source 교차표 출력")
    parser.add_argument("--json", action="store_true", help="JSON 결과도 출력")
    args = parser.parse_args()

    client = MongoClient(args.uri)
    db = client[args.db]

    cat = by_category(db)
    print_table("분야(category)별 응답 현황", cat, "분야")

    src = by_source(db) if args.by_source else None
    if src:
        print_table("출처(source)별 응답 현황", src, "출처")

    if args.cross:
        cross = by_cross(db)
        print(f"\n=== 분야 × 출처 응답 교차표 (submitted 기준) ===")
        print(f"  {'분야':<10s} {'출처':<25s} {'응답':>6s}")
        print(f"  {'-' * 10} {'-' * 25} {'-' * 6}")
        for r in cross:
            print(f"  {str(r['_id']['category']):<10s} {str(r['_id']['source'])[:25]:<25s} {r['submitted']:>6d}")

    if args.json:
        out = {"by_category": [{"label": r["_id"], "pool": r["pool"], "sent": r["sent"], "submitted": r["submitted"]} for r in cat]}
        if src:
            out["by_source"] = [{"label": r["_id"], "pool": r["pool"], "sent": r["sent"], "submitted": r["submitted"]} for r in src]
        print("\n=== JSON ===")
        print(json.dumps(out, ensure_ascii=False, indent=2))

    client.close()


if __name__ == "__main__":
    main()
