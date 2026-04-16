from fastapi import APIRouter, HTTPException, Header
from typing import Optional
from services.db import get_db
from config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_admin(key: Optional[str]):
    if key != get_settings().ADMIN_KEY:
        raise HTTPException(403, "관리자 인증 실패")


@router.get("/stats")
async def get_stats(x_admin_key: Optional[str] = Header(None)):
    _check_admin(x_admin_key)
    db = get_db()

    total_p = await db.participants.count_documents({})
    total_r = await db.responses.count_documents({})

    pipeline = [
        {"$lookup": {
            "from": "responses",
            "localField": "token",
            "foreignField": "token",
            "as": "resp",
        }},
        {"$group": {
            "_id": "$category",
            "participants": {"$sum": 1},
            "responded": {"$sum": {"$cond": [{"$gt": [{"$size": "$resp"}, 0]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    cursor = db.participants.aggregate(pipeline)
    by_category = {}
    async for doc in cursor:
        cat = doc["_id"] or "미분류"
        by_category[cat] = {
            "participants": doc["participants"],
            "responded": doc["responded"],
        }

    return {
        "total_participants": total_p,
        "total_responses": total_r,
        "by_category": by_category,
    }


@router.get("/responses")
async def list_responses(
    x_admin_key: Optional[str] = Header(None),
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
):
    _check_admin(x_admin_key)
    db = get_db()

    pipeline = [
        {"$lookup": {
            "from": "participants",
            "localField": "token",
            "foreignField": "token",
            "as": "participant",
        }},
        {"$unwind": {"path": "$participant", "preserveNullAndEmptyArrays": True}},
    ]
    if category:
        pipeline.append({"$match": {"participant.category": category}})
    pipeline += [
        {"$sort": {"submitted_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "token": 1,
            "survey_version": 1,
            "responses": 1,
            "submitted_at": 1,
            "updated_at": 1,
            "name": "$participant.name",
            "org": "$participant.org",
            "category": "$participant.category",
        }},
    ]
    cursor = db.responses.aggregate(pipeline)
    results = [doc async for doc in cursor]
    return {"count": len(results), "data": results}


@router.get("/export")
async def export_csv(x_admin_key: Optional[str] = Header(None)):
    _check_admin(x_admin_key)
    db = get_db()
    import csv, io
    from fastapi.responses import StreamingResponse

    pipeline = [
        {"$lookup": {
            "from": "participants",
            "localField": "token",
            "foreignField": "token",
            "as": "p",
        }},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        {"$sort": {"submitted_at": 1}},
    ]
    cursor = db.responses.aggregate(pipeline)
    docs = [doc async for doc in cursor]

    if not docs:
        raise HTTPException(404, "응답 데이터가 없습니다.")

    all_keys = set()
    for d in docs:
        all_keys.update(d.get("responses", {}).keys())
    sorted_keys = sorted(all_keys)

    output = io.StringIO()
    writer = csv.writer(output)
    header = ["token", "name", "org", "category", "submitted_at", "updated_at"] + sorted_keys
    writer.writerow(header)

    for d in docs:
        p = d.get("p", {})
        resp = d.get("responses", {})
        row = [
            d.get("token", ""),
            p.get("name", ""),
            p.get("org", ""),
            p.get("category", ""),
            str(d.get("submitted_at", "")),
            str(d.get("updated_at", "")),
        ]
        for k in sorted_keys:
            v = resp.get(k, "")
            row.append(str(v) if v is not None else "")
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=survey_responses.csv"},
    )


@router.get("/participants")
async def list_participants(
    x_admin_key: Optional[str] = Header(None),
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
):
    _check_admin(x_admin_key)
    db = get_db()
    query = {}
    if category:
        query["category"] = category
    cursor = db.participants.find(query, {"_id": 0}).skip(skip).limit(limit)
    results = [doc async for doc in cursor]
    total = await db.participants.count_documents(query)
    return {"total": total, "count": len(results), "data": results}
