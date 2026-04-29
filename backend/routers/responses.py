import logging
import re
import uuid
from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
from uuid import uuid4
from models import (
    ResponseSubmit,
    ResponseRecord,
    ParticipantUpdate,
    SelfRegisterRequest,
    RecoverRequest,
    CommentCreateRequest,
    CommentUpdateRequest,
)
from services.db import get_db
from services.email_service import render_completion, render_email, send_email
from config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["responses"])

# 자가등록·응답 진입 시 허용되는 직군 (Q6 분기와 일치)
ALLOWED_SELF_CATEGORIES = {"설계", "시공", "유지관리", "건축행정"}

# 직군별 응답 정원. 4직군 균등 75부 = 합산 300부.
# 도달 시 해당 직군 신규 자가등록 차단. 4직군 모두 충족되면 전체 마감.
# 연구진(category=연구진)은 정원 외.
QUOTA_PER_CATEGORY = {
    "설계": 75,
    "시공": 75,
    "유지관리": 75,
    "건축행정": 75,
}
SURVEY_LIMIT = sum(QUOTA_PER_CATEGORY.values())  # 300

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


async def _completed_count_by_category(db) -> dict[str, int]:
    """category별 완료 응답 수 (연구진·직원 테스트 제외). QUOTA_PER_CATEGORY 4개 키만 보장."""
    pipeline = [
        {"$match": {"submitted_at": {"$ne": None}}},
        {"$lookup": {
            "from": "participants",
            "localField": "token",
            "foreignField": "token",
            "as": "p",
        }},
        {"$unwind": "$p"},
        {"$match": {
            "p.category": {"$in": list(QUOTA_PER_CATEGORY.keys())},
            "p.source": {"$ne": "staff"},
        }},
        {"$group": {"_id": "$p.category", "count": {"$sum": 1}}},
    ]
    by_cat = {k: 0 for k in QUOTA_PER_CATEGORY}
    async for doc in db.responses.aggregate(pipeline):
        by_cat[doc["_id"]] = doc["count"]
    return by_cat


async def _is_category_full(db, category: str) -> bool:
    """해당 직군이 정원 충족(>= quota)되었는지."""
    if category not in QUOTA_PER_CATEGORY:
        return False
    by_cat = await _completed_count_by_category(db)
    return by_cat.get(category, 0) >= QUOTA_PER_CATEGORY[category]


async def _is_survey_closed(db) -> bool:
    """4직군 모두 정원 충족 시 전체 마감."""
    by_cat = await _completed_count_by_category(db)
    return all(by_cat.get(c, 0) >= q for c, q in QUOTA_PER_CATEGORY.items())


async def _send_completion_email(participant: dict, token: str) -> None:
    """응답 제출 직후 자동 발송. 실패해도 응답 처리는 영향받지 않음."""
    s = get_settings()
    if not s.GMAIL_USER or not s.GMAIL_APP_PASSWORD:
        return
    if not participant.get("email"):
        return
    db = get_db()
    review_url = f"{s.SURVEY_BASE_URL}/?token={token}&review=1"
    subject = "[AURI 건축AI 실무자 조사] 응답 완료 안내 — 내 응답 확인 링크"
    html = render_completion(
        participant.get("name") or participant.get("reward_name", "") or "응답자",
        participant.get("org", ""),
        review_url,
    )
    now = datetime.utcnow()
    log_doc = {
        "batch_id": "auto-completion",
        "token": token,
        "email": participant["email"],
        "name": participant.get("name", ""),
        "org": participant.get("org", ""),
        "category": participant.get("category", ""),
        "type": "completion",
        "subject": subject,
        "admin_email": "system",
        "admin_name": "자동 발송",
        "sent_at": now,
    }
    try:
        send_email(participant["email"], subject, html)
        log_doc.update({"status": "sent", "error": ""})
        await db.email_logs.insert_one(log_doc)
    except Exception as e:
        err = str(e)
        logger.warning(f"Completion email failed for {participant['email']}: {err}")
        log_doc.update({"status": "failed", "error": err})
        try:
            await db.email_logs.insert_one(log_doc)
        except Exception:
            pass


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


@router.get("/survey/status")
async def survey_status():
    """공개 — 직군별 완료 수·정원·마감 여부. 인트로/자가등록 화면에 표시."""
    db = get_db()
    by_cat = await _completed_count_by_category(db)
    completed = sum(by_cat.values())
    return {
        "completed": completed,
        "limit": SURVEY_LIMIT,
        "is_closed": completed >= SURVEY_LIMIT or all(
            by_cat.get(c, 0) >= q for c, q in QUOTA_PER_CATEGORY.items()
        ),
        "by_category": [
            {
                "category": c,
                "completed": by_cat.get(c, 0),
                "quota": q,
                "is_full": by_cat.get(c, 0) >= q,
            }
            for c, q in QUOTA_PER_CATEGORY.items()
        ],
    }


@router.get("/survey/{token}")
async def verify_token(token: str):
    db = get_db()
    participant = await db.participants.find_one({"token": token}, {"_id": 0})
    if not participant:
        raise HTTPException(404, "유효하지 않은 설문 링크입니다.")

    # 마감 후 신규 진입·이어작성·리뷰 모두 차단 (연구진 토큰은 예외)
    if participant.get("category") != "연구진" and await _is_survey_closed(db):
        raise HTTPException(
            410,
            f"설문이 마감되었습니다. (4직군 합산 {SURVEY_LIMIT}부 도달) 참여해 주셔서 감사합니다.",
        )

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
        "source": participant.get("source", "imported"),
        "consent_pi": bool(participant.get("consent_pi", False)),
        "consent_reward": bool(participant.get("consent_reward", False)),
        "reward_name": participant.get("reward_name", ""),
        "reward_phone": participant.get("reward_phone", ""),
        "has_responded": has_submitted,
        "responses": existing.get("responses") if has_submitted else None,
        "comments": existing.get("comments") if existing else None,
        "submitted_at": existing.get("submitted_at").isoformat() if has_submitted else None,
        "updated_at": existing.get("updated_at").isoformat() if existing and existing.get("updated_at") else None,
    }


# ── 공개 자가등록 (No Auth) ──

@router.post("/survey/register")
async def self_register(body: SelfRegisterRequest, request: Request):
    """공개 단일 링크에서 응답자가 직접 정보를 입력하고 토큰을 발급받는다.
    - email은 필수 (완료 안내 메일 발송용).
    - 직군(category)은 4직군 중 하나. 해당 직군 정원이 충족되면 신규 등록 차단.
    - 사례품 동의(consent_reward) 시에만 reward_name·reward_phone 수집.
    - 토큰은 random uuid. 신규 email은 새 토큰 발급.
    - imported 명단 & 미응답: 폼 입력값으로 정보 갱신 + source 전환 + 기존 토큰 노출(smooth 진입).
      신원 사칭 방지를 위해 폼 입력값이 imported 정보를 덮어씀. 정원 검사는 건너뜀
      (이미 명단에 있던 사람이므로 신규 점유 아님).
    - 이미 응답 완료: 차단 (재등록 의미 없음, /recover로 리뷰 링크).
    - 이미 self/staff 등록: 차단 (분실 시 /recover).
    """
    s = get_settings()

    email = (body.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "올바른 이메일을 입력해 주십시오.")
    if not body.consent_pi:
        raise HTTPException(400, "이메일 수집·이용에 동의해 주셔야 참여하실 수 있습니다.")
    if body.category not in ALLOWED_SELF_CATEGORIES:
        raise HTTPException(400, "직군(설계/시공/유지관리/건축행정)을 선택해 주십시오.")
    if not (body.org or "").strip():
        raise HTTPException(400, "소속 기관·회사명을 입력해 주십시오.")
    if body.consent_reward:
        if not body.reward_name.strip() or not body.reward_phone.strip():
            raise HTTPException(400, "사례품 수령자명과 휴대폰 번호를 입력해 주십시오.")

    db = get_db()
    existing = await db.participants.find_one({"email": email})

    # 응답 완료자·self/staff 기등록자는 차단 — 분실 시 /recover.
    # 응답 완료 여부는 participants에 필드가 없으므로 responses 컬렉션을 직접 조회.
    if existing:
        existing_resp = await db.responses.find_one(
            {"token": existing["token"], "submitted_at": {"$ne": None}},
            {"_id": 1},
        )
        if existing_resp:
            raise HTTPException(
                409,
                "이 이메일로 이미 응답을 제출하셨습니다. 응답 확인·수정은 '토큰 재발송'을 요청해 메일의 리뷰 링크로 접속해 주십시오.",
            )
        if existing.get("source") in ("self", "staff"):
            raise HTTPException(
                409,
                "이 이메일로 이미 등록되어 있습니다. 처음 등록 시 받으신 메일의 링크로 접속하시거나, 메일을 못 받으셨다면 '토큰 재발송'을 요청해 주십시오.",
            )

    # 직원 테스트(is_staff=true) + imported promote는 정원·마감 검사 모두 건너뜀.
    if not body.is_staff and not existing:
        if await _is_survey_closed(db):
            raise HTTPException(
                410,
                f"설문이 마감되었습니다. (4직군 합산 {SURVEY_LIMIT}부 도달) 참여해 주셔서 감사합니다.",
            )
        if await _is_category_full(db, body.category):
            raise HTTPException(
                409,
                f"'{body.category}' 직군 응답 정원({QUOTA_PER_CATEGORY[body.category]}부)이 충족되어 신규 참여가 마감되었습니다.",
            )

    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    name = (body.reward_name or "").strip() if body.consent_reward else ""

    # imported 명단 & 미응답 → 폼 입력값으로 정보 갱신 후 기존 토큰 노출 (smooth 진입).
    if existing:
        token = existing["token"]
        last_backup = await db.participants_backup.find_one(
            {"token": token}, sort=[("version", -1)]
        )
        next_version = (last_backup.get("version", 0) + 1) if last_backup else 1
        snapshot = {k: v for k, v in existing.items() if k != "_id"}
        await db.participants_backup.insert_one({
            "token": token,
            "version": next_version,
            "backed_up_at": now,
            "ip": ip,
            "user_agent": ua,
            "snapshot": snapshot,
            "source_action": "self_register_promote",
        })

        update_fields = {
            "name": name,
            "org": body.org.strip(),
            "category": body.category,
            "dept": (body.dept or "").strip(),
            "team": (body.team or "").strip(),
            "position": (body.position or "").strip(),
            "rank": (body.rank or "").strip(),
            "duty": (body.duty or "").strip(),
            "source": "staff" if body.is_staff else "self",
            "consent_pi": True,
            "consent_pi_at": now,
            "consent_reward": bool(body.consent_reward),
            "consent_reward_at": now if body.consent_reward else None,
            "reward_name": body.reward_name.strip() if body.consent_reward else "",
            "reward_phone": body.reward_phone.strip() if body.consent_reward else "",
            "register_ip": ip,
            "register_ua": ua,
            "register_updated_at": now,
            "self_registered_at": now,
            "updated_at": now,
        }
        await db.participants.update_one({"token": token}, {"$set": update_fields})

        return {
            "status": "promoted",
            "token": token,
            "survey_url": f"{s.SURVEY_BASE_URL}/?token={token}",
        }

    token = uuid.uuid4().hex[:16]

    doc = {
        "token": token,
        "email": email,
        "name": name,
        "org": body.org.strip(),
        "category": body.category,
        "field": "",
        "dept": (body.dept or "").strip(),
        "team": (body.team or "").strip(),
        "position": (body.position or "").strip(),
        "rank": (body.rank or "").strip(),
        "duty": (body.duty or "").strip(),
        "phone": "",
        "source": "staff" if body.is_staff else "self",
        "consent_pi": True,
        "consent_pi_at": now,
        "consent_reward": bool(body.consent_reward),
        "consent_reward_at": now if body.consent_reward else None,
        "reward_name": body.reward_name.strip() if body.consent_reward else "",
        "reward_phone": body.reward_phone.strip() if body.consent_reward else "",
        "register_ip": ip,
        "register_ua": ua,
        "register_updated_at": now,
        "created_at": now,
    }
    await db.participants.insert_one(doc)

    return {
        "status": "created",
        "token": token,
        "survey_url": f"{s.SURVEY_BASE_URL}/?token={token}",
    }


@router.post("/survey/recover")
async def recover_token(body: RecoverRequest):
    """자가등록자가 토큰 링크를 분실한 경우, 등록 시 사용한 email로 토큰 링크를 재발송한다.
    - 응답에는 토큰을 노출하지 않는다 (메일 수신만이 본인 확인 메커니즘).
    - 등록 여부와 무관하게 동일한 응답을 반환해 email 정찰을 어렵게 한다.
    - 응답 미제출이면 '설문 시작 링크', 제출 완료면 '응답 확인·수정 링크'를 발송한다.
    """
    s = get_settings()
    email = (body.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "올바른 이메일을 입력해 주십시오.")

    db = get_db()
    participant = await db.participants.find_one({"email": email}, {"_id": 0})
    if not participant:
        return {"status": "sent"}

    token = participant["token"]
    name = participant.get("name") or participant.get("reward_name") or "응답자"
    org = participant.get("org", "")

    existing_resp = await db.responses.find_one({"token": token}, {"submitted_at": 1})
    has_submitted = bool(existing_resp and existing_resp.get("submitted_at"))

    if has_submitted:
        review_url = f"{s.SURVEY_BASE_URL}/?token={token}&review=1"
        subject = "[AURI 건축AI 실무자 조사] 응답 확인·수정 링크 재발송"
        html = render_completion(name, org, review_url)
    else:
        survey_url = f"{s.SURVEY_BASE_URL}/?token={token}"
        subject = "[AURI 건축AI 실무자 조사] 설문 참여 링크 재발송"
        html = render_email(name, org, survey_url)

    log_doc = {
        "batch_id": "auto-recovery",
        "token": token,
        "email": email,
        "name": participant.get("name", ""),
        "org": org,
        "category": participant.get("category", ""),
        "type": "recovery",
        "subject": subject,
        "admin_email": "system",
        "admin_name": "자동 재발송",
        "sent_at": datetime.utcnow(),
    }
    try:
        send_email(email, subject, html)
        log_doc.update({"status": "sent", "error": ""})
    except Exception as e:
        log_doc.update({"status": "failed", "error": str(e)})
    await db.email_logs.insert_one(log_doc)

    return {"status": "sent"}


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
                "survey_version": "v11",
                "responses": {},
                "submitted_at": None,
            },
        },
        upsert=True,
    )
    return {"status": "ok", "comments_count": len(comments), "updated_at": now.isoformat()}


# ── Review Comment Threads (연구진 + 관리자 공유) ──

@router.get("/survey/{token}/threads")
async def list_threads(token: str, survey_version: str = "v11"):
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
    survey_version: str = "v11",
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

    # 마감 후 신규 제출·기제출 수정 모두 차단 (연구진 토큰은 예외)
    if participant.get("category") != "연구진" and await _is_survey_closed(db):
        raise HTTPException(
            410,
            f"설문이 마감되었습니다. (4직군 합산 {SURVEY_LIMIT}부 도달) 참여해 주셔서 감사합니다.",
        )

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
        if participant.get("category") != "연구진":
            await _send_completion_email(participant, body.token)
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
    if participant.get("category") != "연구진":
        await _send_completion_email(participant, body.token)
    return {"status": "created", "token": body.token}


@router.get("/responses/{token}")
async def get_response(token: str):
    db = get_db()
    doc = await db.responses.find_one({"token": token}, {"_id": 0})
    if not doc:
        return {"token": token, "responses": None}
    return doc
