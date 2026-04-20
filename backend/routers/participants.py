from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4
from models import CommentCreateRequest, CommentUpdateRequest, COMMENT_STATUSES
from services.db import get_db
from services.email_service import render_email, send_email
from services.admin_auth import verify_admin_token
from config import get_settings
import logging

logger = logging.getLogger(__name__)

ALLOWED_EMAIL_TYPES = {"invite", "reminder", "deadline", "custom"}

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/me")
async def whoami(admin: dict = Depends(verify_admin_token)):
    return admin


@router.get("/stats")
async def get_stats(admin: dict = Depends(verify_admin_token)):
    db = get_db()

    total_p = await db.participants.count_documents({})
    total_r = await db.responses.count_documents({"submitted_at": {"$ne": None}})

    submitted_filter = {
        "$filter": {
            "input": "$resp",
            "as": "r",
            "cond": {"$ne": ["$$r.submitted_at", None]},
        }
    }
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
            "responded": {"$sum": {"$cond": [{"$gt": [{"$size": submitted_filter}, 0]}, 1, 0]}},
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
    admin: dict = Depends(verify_admin_token),
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
):
    db = get_db()

    pipeline = [
        {"$match": {"submitted_at": {"$ne": None}}},
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
        {"$lookup": {
            "from": "review_comments",
            "let": {"tk": "$token"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$author_token", "$$tk"]}}},
            ],
            "as": "rc",
        }},
        {"$sort": {"submitted_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "token": 1,
            "survey_version": 1,
            "responses": 1,
            "comments": 1,
            "submitted_at": 1,
            "updated_at": 1,
            "name": "$participant.name",
            "org": "$participant.org",
            "category": "$participant.category",
            "comment_count": {"$size": "$rc"},
        }},
    ]
    cursor = db.responses.aggregate(pipeline)
    results = [doc async for doc in cursor]
    return {"count": len(results), "data": results}


@router.get("/export")
async def export_csv(admin: dict = Depends(verify_admin_token)):
    db = get_db()
    import csv, io, json as _json
    from fastapi.responses import StreamingResponse

    pipeline = [
        {"$match": {"submitted_at": {"$ne": None}}},
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
            if isinstance(v, (list, dict)):
                row.append(_json.dumps(v, ensure_ascii=False))
            else:
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
    admin: dict = Depends(verify_admin_token),
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
):
    db = get_db()
    match: dict = {}
    if category:
        match["category"] = category

    pipeline = [
        {"$match": match},
        {"$lookup": {
            "from": "responses",
            "let": {"tk": "$token"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$token", "$$tk"]},
                    {"$ne": ["$submitted_at", None]},
                ]}}},
            ],
            "as": "resp",
        }},
        {"$addFields": {
            "responded": {"$gt": [{"$size": "$resp"}, 0]},
            "response_submitted_at": {"$arrayElemAt": ["$resp.submitted_at", 0]},
            "response_updated_at": {"$arrayElemAt": ["$resp.updated_at", 0]},
        }},
        {"$project": {"resp": 0, "_id": 0}},
        {"$skip": skip},
        {"$limit": limit},
    ]
    cursor = db.participants.aggregate(pipeline)
    results = [doc async for doc in cursor]
    total = await db.participants.count_documents(match)
    return {"total": total, "count": len(results), "data": results}


# ── Email ──

class EmailSendRequest(BaseModel):
    tokens: list[str]
    subject: str = "건축 분야 AI 설문조사 참여 요청 (AURI)"
    type: str = "invite"  # invite | reminder | deadline | custom


@router.post("/email/preview", response_class=HTMLResponse)
async def email_preview(admin: dict = Depends(verify_admin_token)):
    s = get_settings()
    url = f"{s.SURVEY_BASE_URL}/?token=SAMPLE_TOKEN"
    return render_email("홍길동", "예시기관", url)


@router.post("/email/send")
async def send_survey_emails(
    body: EmailSendRequest,
    admin: dict = Depends(verify_admin_token),
):
    s = get_settings()
    if not s.GMAIL_USER or not s.GMAIL_APP_PASSWORD:
        raise HTTPException(500, "Gmail 설정이 없습니다.")

    email_type = body.type if body.type in ALLOWED_EMAIL_TYPES else "custom"
    batch_id = uuid4().hex[:12]
    db = get_db()
    results = {
        "batch_id": batch_id,
        "type": email_type,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    for token in body.tokens:
        p = await db.participants.find_one({"token": token})
        if not p:
            results["skipped"] += 1
            continue

        survey_url = f"{s.SURVEY_BASE_URL}/?token={token}"
        html = render_email(p.get("name", ""), p.get("org", ""), survey_url)
        now = datetime.now(timezone.utc)

        log_doc = {
            "batch_id": batch_id,
            "token": token,
            "email": p["email"],
            "name": p.get("name", ""),
            "org": p.get("org", ""),
            "category": p.get("category", ""),
            "type": email_type,
            "subject": body.subject,
            "admin_email": admin.get("email", ""),
            "admin_name": admin.get("name", ""),
            "sent_at": now,
        }

        try:
            send_email(p["email"], body.subject, html)
            log_doc.update({"status": "sent", "error": ""})
            await db.email_logs.insert_one(log_doc)
            await db.participants.update_one(
                {"token": token},
                [{"$set": {
                    "email_sent": True,
                    "email_sent_at": now,  # legacy compat
                    "email_last_sent_at": now,
                    "email_first_sent_at": {"$ifNull": ["$email_first_sent_at", now]},
                    "email_sent_count": {"$add": [{"$ifNull": ["$email_sent_count", 0]}, 1]},
                    "email_last_status": "sent",
                    "email_last_type": email_type,
                    "email_last_error": "",
                }}],
            )
            results["sent"] += 1
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Email failed for {p['email']}: {err_msg}")
            log_doc.update({"status": "failed", "error": err_msg})
            await db.email_logs.insert_one(log_doc)
            await db.participants.update_one(
                {"token": token},
                {"$set": {
                    "email_last_status": "failed",
                    "email_last_attempt_at": now,
                    "email_last_type": email_type,
                    "email_last_error": err_msg,
                }},
            )
            results["failed"] += 1
            results["errors"].append({"token": token, "email": p["email"], "error": err_msg})

    return results


@router.get("/email/logs")
async def list_email_logs(
    admin: dict = Depends(verify_admin_token),
    token: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    batch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 200,
):
    db = get_db()
    query: dict = {}
    if token:
        query["token"] = token
    if status:
        query["status"] = status
    if type:
        query["type"] = type
    if batch_id:
        query["batch_id"] = batch_id

    total = await db.email_logs.count_documents(query)
    cursor = (
        db.email_logs.find(query, {"_id": 0})
        .sort("sent_at", -1)
        .skip(skip)
        .limit(min(limit, 1000))
    )
    items = [doc async for doc in cursor]
    return {"total": total, "count": len(items), "data": items}


@router.get("/email/history")
async def email_history(
    admin: dict = Depends(verify_admin_token),
    category: Optional[str] = None,
):
    db = get_db()
    p_query: dict = {"email_sent": True}
    if category:
        p_query["category"] = category
    sent_count = await db.participants.count_documents(p_query)
    total = await db.participants.count_documents({"category": category} if category else {})

    log_total = await db.email_logs.count_documents({})
    log_sent = await db.email_logs.count_documents({"status": "sent"})
    log_failed = await db.email_logs.count_documents({"status": "failed"})

    by_type_cursor = db.email_logs.aggregate([
        {"$match": {"status": "sent"}},
        {"$group": {"_id": "$type", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])
    by_type = {doc["_id"] or "unknown": doc["count"] async for doc in by_type_cursor}

    return {
        "unique_recipients_sent": sent_count,
        "total_participants": total,
        "log_total": log_total,
        "log_sent": log_sent,
        "log_failed": log_failed,
        "by_type": by_type,
    }


# ── Admin Management (admins 관리) ──

class AdminCreateRequest(BaseModel):
    email: str
    name: str = ""
    role: str = "manager"


@router.get("/admins")
async def list_admins(admin: dict = Depends(verify_admin_token)):
    db = get_db()
    cursor = db.admins.find({}, {"_id": 0}).sort("created_at", -1)
    items = [doc async for doc in cursor]
    return {"count": len(items), "data": items}


@router.post("/admins")
async def create_admin(body: AdminCreateRequest, admin: dict = Depends(verify_admin_token)):
    from services.token_service import generate_admin_token
    s = get_settings()
    db = get_db()
    email = body.email.lower().strip()
    token = generate_admin_token(email, s.ADMIN_TOKEN_SECRET)
    doc = {
        "email": email,
        "name": body.name,
        "role": body.role,
        "token": token,
        "active": True,
        "created_at": datetime.now(timezone.utc),
        "created_by": admin.get("email"),
    }
    await db.admins.update_one({"email": email}, {"$setOnInsert": doc}, upsert=True)
    saved = await db.admins.find_one({"email": email}, {"_id": 0})
    return {"status": "ok", "admin": saved, "admin_url": f"{s.ADMIN_BASE_URL}/?token={token}"}


# ── Review Comment Threads (관리자 측) ──

def _serialize_comment(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    for k in ("created_at", "updated_at", "status_changed_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


@router.get("/threads")
async def admin_list_threads(
    admin: dict = Depends(verify_admin_token),
    survey_version: str = "v7",
    qid: Optional[str] = None,
    status: Optional[str] = None,
):
    """모든 코멘트 스레드를 조회. qid로 필터링하면 단일 문항만, 없으면 전체를 qid별로 그룹화."""
    db = get_db()
    query: dict = {"survey_version": survey_version}
    if qid:
        query["qid"] = qid
    if status:
        query["status"] = status

    cursor = db.review_comments.find(query, {"_id": 0}).sort("created_at", 1)
    by_qid: dict[str, list[dict]] = {}
    total = 0
    async for doc in cursor:
        out = _serialize_comment(doc)
        by_qid.setdefault(out["qid"], []).append(out)
        total += 1

    return {
        "survey_version": survey_version,
        "total": total,
        "qid_count": len(by_qid),
        "threads": by_qid,
    }


@router.post("/threads/{qid}")
async def admin_create_comment(
    qid: str,
    body: CommentCreateRequest,
    admin: dict = Depends(verify_admin_token),
    survey_version: str = "v7",
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "내용을 입력해 주십시오.")
    db = get_db()
    if body.parent_id:
        parent = await db.review_comments.find_one({"id": body.parent_id})
        if not parent:
            raise HTTPException(404, "원본 코멘트를 찾을 수 없습니다.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    doc = {
        "id": uuid4().hex,
        "survey_version": survey_version,
        "qid": qid,
        "author_role": "admin",
        "author_token": admin.get("token", ""),
        "author_name": admin.get("name", ""),
        "author_email": admin.get("email", ""),
        "author_org": "관리자",
        "text": text,
        "status": "open",
        "parent_id": body.parent_id,
        "created_at": now,
        "updated_at": None,
        "status_changed_at": None,
        "status_changed_by": "",
    }
    await db.review_comments.insert_one(doc)
    return {"status": "created", "comment": _serialize_comment(doc)}


@router.patch("/threads/{qid}/{comment_id}")
async def admin_update_comment(
    qid: str,
    comment_id: str,
    body: CommentUpdateRequest,
    admin: dict = Depends(verify_admin_token),
):
    """관리자: 본문 수정(본인 글만) 또는 상태 변경(누구 글이든 가능)."""
    db = get_db()
    target = await db.review_comments.find_one({"id": comment_id, "qid": qid})
    if not target:
        raise HTTPException(404, "코멘트를 찾을 수 없습니다.")

    update_fields: dict = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if body.text is not None:
        if target.get("author_token") != admin.get("token"):
            raise HTTPException(403, "본인이 작성한 코멘트만 본문 수정할 수 있습니다.")
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "내용을 입력해 주십시오.")
        update_fields["text"] = text
        update_fields["updated_at"] = now

    if body.status is not None:
        if body.status not in COMMENT_STATUSES:
            raise HTTPException(400, f"허용되지 않은 상태입니다: {body.status}")
        update_fields["status"] = body.status
        update_fields["status_changed_at"] = now
        update_fields["status_changed_by"] = admin.get("name", admin.get("email", ""))

    if not update_fields:
        raise HTTPException(400, "수정할 내용이 없습니다.")

    await db.review_comments.update_one({"id": comment_id}, {"$set": update_fields})
    updated = await db.review_comments.find_one({"id": comment_id}, {"_id": 0})
    return {"status": "updated", "comment": _serialize_comment(updated)}


@router.delete("/threads/{qid}/{comment_id}")
async def admin_delete_comment(
    qid: str,
    comment_id: str,
    admin: dict = Depends(verify_admin_token),
):
    """관리자: 본인 글이거나 role=owner면 삭제 가능. 답글 있으면 soft-delete."""
    db = get_db()
    target = await db.review_comments.find_one({"id": comment_id, "qid": qid})
    if not target:
        raise HTTPException(404, "코멘트를 찾을 수 없습니다.")

    is_owner_role = admin.get("role") == "owner"
    is_author = target.get("author_token") == admin.get("token")
    if not (is_owner_role or is_author):
        raise HTTPException(403, "본인 글이 아니면 owner 권한이 필요합니다.")

    has_replies = await db.review_comments.count_documents({"parent_id": comment_id}) > 0
    if has_replies:
        await db.review_comments.update_one(
            {"id": comment_id},
            {"$set": {
                "text": "(관리자가 삭제한 코멘트)",
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }},
        )
        return {"status": "soft_deleted"}

    await db.review_comments.delete_one({"id": comment_id})
    return {"status": "deleted"}
