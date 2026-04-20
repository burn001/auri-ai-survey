from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
from uuid import uuid4
from models import (
    ResponseSubmit,
    ResponseRecord,
    ParticipantUpdate,
    CommentCreateRequest,
    CommentUpdateRequest,
)
from services.db import get_db

router = APIRouter(prefix="/api", tags=["responses"])


async def _require_reviewer(token: str) -> dict:
    """token이 유효한 연구진 참가자인지 확인하고 participant doc 반환."""
    db = get_db()
    p = await db.participants.find_one({"token": token}, {"_id": 0})
    if not p:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")
    if p.get("category") != "연구진":
        raise HTTPException(403, "연구진 전용 기능입니다.")
    return p


def _serialize_comment(doc: dict) -> dict:
    """ObjectId 제거 + datetime ISO 변환."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    for k in ("created_at", "updated_at", "status_changed_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


@router.get("/survey/{token}")
async def verify_token(token: str):
    db = get_db()
    participant = await db.participants.find_one({"token": token}, {"_id": 0})
    if not participant:
        raise HTTPException(404, "유효하지 않은 설문 링크입니다.")

    existing = await db.responses.find_one({"token": token}, {"_id": 0})
    has_submitted = bool(existing and existing.get("submitted_at"))
    return {
        "token": participant["token"],
        "name": participant.get("name", ""),
        "email": participant.get("email", ""),
        "org": participant.get("org", ""),
        "category": participant.get("category", ""),
        "field": participant.get("field", ""),
        "phone": participant.get("phone", ""),
        "dept": participant.get("dept", ""),
        "team": participant.get("team", ""),
        "position": participant.get("position", ""),
        "rank": participant.get("rank", ""),
        "duty": participant.get("duty", ""),
        "has_responded": has_submitted,
        "responses": existing.get("responses") if has_submitted else None,
        "comments": existing.get("comments") if existing else None,
        "submitted_at": existing.get("submitted_at").isoformat() if has_submitted else None,
        "updated_at": existing.get("updated_at").isoformat() if existing and existing.get("updated_at") else None,
    }


@router.patch("/survey/{token}/comments")
async def save_reviewer_comments(token: str, body: dict, request: Request):
    """연구진 전용 — 제출 전에 문항별 수정 요청 메모를 자동 저장한다.
    responses 문서를 upsert하되, 새로 만들 때는 submitted_at을 세팅하지 않아 '제출'과 구분한다.
    """
    db = get_db()
    participant = await db.participants.find_one({"token": token}, {"_id": 0})
    if not participant:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")
    if participant.get("category") != "연구진":
        raise HTTPException(403, "연구진 전용 엔드포인트입니다.")

    comments = body.get("comments")
    if not isinstance(comments, dict):
        raise HTTPException(400, "comments 필드가 올바르지 않습니다.")

    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    await db.responses.update_one(
        {"token": token},
        {
            "$set": {
                "comments": comments,
                "comments_updated_at": now,
                "ip": ip,
                "user_agent": ua,
            },
            "$setOnInsert": {
                "token": token,
                "survey_version": "v7",
                "responses": {},
                "submitted_at": None,
            },
        },
        upsert=True,
    )
    return {"status": "ok", "comments_count": len(comments), "updated_at": now.isoformat()}


# ── Review Comment Threads (연구진 + 관리자 공유) ──

@router.get("/survey/{token}/threads")
async def list_threads(token: str, survey_version: str = "v7"):
    """연구진 토큰으로 모든 코멘트 스레드를 조회한다.
    qid별로 그룹화하여 반환. 모든 작성자(다른 연구진 + 관리자)의 코멘트를 포함.
    """
    await _require_reviewer(token)
    db = get_db()
    cursor = db.review_comments.find(
        {"survey_version": survey_version},
        {"_id": 0},
    ).sort("created_at", 1)

    by_qid: dict[str, list[dict]] = {}
    async for doc in cursor:
        out = _serialize_comment(doc)
        by_qid.setdefault(out["qid"], []).append(out)
    return {"survey_version": survey_version, "threads": by_qid}


@router.post("/survey/{token}/threads/{qid}")
async def create_comment(
    token: str,
    qid: str,
    body: CommentCreateRequest,
    survey_version: str = "v7",
):
    """연구진이 새 코멘트(또는 답글)를 작성한다."""
    p = await _require_reviewer(token)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "내용을 입력해 주십시오.")

    db = get_db()
    if body.parent_id:
        parent = await db.review_comments.find_one({"id": body.parent_id})
        if not parent:
            raise HTTPException(404, "원본 코멘트를 찾을 수 없습니다.")

    now = datetime.utcnow()
    doc = {
        "id": uuid4().hex,
        "survey_version": survey_version,
        "qid": qid,
        "author_role": "reviewer",
        "author_token": token,
        "author_name": p.get("name", ""),
        "author_email": p.get("email", ""),
        "author_org": p.get("org", ""),
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


@router.patch("/survey/{token}/threads/{qid}/{comment_id}")
async def update_own_comment(
    token: str,
    qid: str,
    comment_id: str,
    body: CommentUpdateRequest,
):
    """본인이 작성한 코멘트의 본문만 수정 가능. 상태 변경은 관리자 전용."""
    await _require_reviewer(token)
    if body.status is not None:
        raise HTTPException(403, "상태 변경은 관리자만 가능합니다.")
    text = (body.text or "").strip() if body.text is not None else None
    if not text:
        raise HTTPException(400, "내용을 입력해 주십시오.")

    db = get_db()
    target = await db.review_comments.find_one({"id": comment_id, "qid": qid})
    if not target:
        raise HTTPException(404, "코멘트를 찾을 수 없습니다.")
    if target.get("author_token") != token:
        raise HTTPException(403, "본인이 작성한 코멘트만 수정할 수 있습니다.")

    now = datetime.utcnow()
    await db.review_comments.update_one(
        {"id": comment_id},
        {"$set": {"text": text, "updated_at": now}},
    )
    updated = await db.review_comments.find_one({"id": comment_id}, {"_id": 0})
    return {"status": "updated", "comment": _serialize_comment(updated)}


@router.delete("/survey/{token}/threads/{qid}/{comment_id}")
async def delete_own_comment(token: str, qid: str, comment_id: str):
    """본인이 작성한 코멘트 삭제. 답글이 달린 경우에도 본문은 비우지만 entry는 유지."""
    await _require_reviewer(token)
    db = get_db()
    target = await db.review_comments.find_one({"id": comment_id, "qid": qid})
    if not target:
        raise HTTPException(404, "코멘트를 찾을 수 없습니다.")
    if target.get("author_token") != token:
        raise HTTPException(403, "본인이 작성한 코멘트만 삭제할 수 있습니다.")

    has_replies = await db.review_comments.count_documents({"parent_id": comment_id}) > 0
    if has_replies:
        await db.review_comments.update_one(
            {"id": comment_id},
            {"$set": {"text": "(작성자가 삭제한 코멘트)", "updated_at": datetime.utcnow()}},
        )
        return {"status": "soft_deleted"}

    await db.review_comments.delete_one({"id": comment_id})
    return {"status": "deleted"}


@router.patch("/survey/{token}/participant")
async def update_participant(token: str, body: ParticipantUpdate, request: Request):
    db = get_db()
    current = await db.participants.find_one({"token": token})
    if not current:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")

    update_fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not update_fields:
        raise HTTPException(400, "수정할 필드가 없습니다.")

    if "email" in update_fields and update_fields["email"] != current.get("email"):
        clash = await db.participants.find_one({
            "email": update_fields["email"],
            "token": {"$ne": token},
        })
        if clash:
            raise HTTPException(409, "이미 사용 중인 이메일입니다.")

    now = datetime.utcnow()
    last_backup = await db.participants_backup.find_one(
        {"token": token}, sort=[("version", -1)]
    )
    next_version = (last_backup.get("version", 0) + 1) if last_backup else 1

    snapshot = {k: v for k, v in current.items() if k != "_id"}
    await db.participants_backup.insert_one({
        "token": token,
        "version": next_version,
        "backed_up_at": now,
        "ip": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
        "snapshot": snapshot,
    })

    update_fields["updated_at"] = now
    await db.participants.update_one({"token": token}, {"$set": update_fields})

    updated = await db.participants.find_one({"token": token}, {"_id": 0})
    return {
        "status": "updated",
        "backup_version": next_version,
        "participant": {
            "token": updated["token"],
            "name": updated.get("name", ""),
            "email": updated.get("email", ""),
            "org": updated.get("org", ""),
            "phone": updated.get("phone", ""),
            "category": updated.get("category", ""),
            "dept": updated.get("dept", ""),
            "team": updated.get("team", ""),
            "position": updated.get("position", ""),
            "rank": updated.get("rank", ""),
            "duty": updated.get("duty", ""),
        },
    }


@router.post("/responses")
async def submit_response(body: ResponseSubmit, request: Request):
    db = get_db()
    participant = await db.participants.find_one({"token": body.token})
    if not participant:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")

    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    comments = body.comments or {}

    existing = await db.responses.find_one({"token": body.token})
    if existing and existing.get("submitted_at"):
        update_fields = {
            "responses": body.responses,
            "survey_version": body.survey_version,
            "updated_at": now,
            "ip": ip,
            "user_agent": ua,
        }
        if body.comments is not None:
            update_fields["comments"] = comments
        await db.responses.update_one(
            {"token": body.token},
            {"$set": update_fields},
        )
        return {"status": "updated", "token": body.token}

    if existing:
        # comment-only upsert 문서가 있었음 — submitted_at만 세팅해 '최초 제출'로 마크
        update_fields = {
            "responses": body.responses,
            "survey_version": body.survey_version,
            "submitted_at": now,
            "ip": ip,
            "user_agent": ua,
        }
        if body.comments is not None:
            update_fields["comments"] = comments
        await db.responses.update_one(
            {"token": body.token},
            {"$set": update_fields},
        )
        return {"status": "created", "token": body.token}

    record = ResponseRecord(
        token=body.token,
        survey_version=body.survey_version,
        responses=body.responses,
        comments=comments,
        submitted_at=now,
        ip=ip,
        user_agent=ua,
    )
    await db.responses.insert_one(record.model_dump())
    return {"status": "created", "token": body.token}


@router.get("/responses/{token}")
async def get_response(token: str):
    db = get_db()
    doc = await db.responses.find_one({"token": token}, {"_id": 0})
    if not doc:
        return {"token": token, "responses": None}
    return doc
