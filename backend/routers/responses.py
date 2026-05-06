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
    IdentityFillRequest,
    CommentCreateRequest,
    CommentUpdateRequest,
    RewardConsentPatch,
)
from services.db import get_db
from services.email_service import render_completion, render_email, send_email
from config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["responses"])

# 자가등록·응답 진입 시 허용되는 직군 (Q6 분기와 일치)
ALLOWED_SELF_CATEGORIES = {"설계", "시공", "유지관리", "건축행정"}

# 직군별 사례품 동의 응답 정원. 4직군 균등 75부 = 합산 300부.
# 카운트 정의: 'consent_reward=true 인 응답 완료자'. 미동의자는 정원에 잡히지 않음.
# 도달 시 해당 직군 신규 자가등록만 차단 — 이미 토큰을 받아 진행 중인 응답자는
# 끝까지 제출·사례품 지급 가능 (verify_token / submit_response는 마감 검사 안 함).
# 4직군 모두 충족되면 신규 자가등록 전체 마감. 연구진(category=연구진)은 정원 외.
QUOTA_PER_CATEGORY = {
    "설계": 75,
    "시공": 75,
    "유지관리": 75,
    "건축행정": 75,
}
SURVEY_LIMIT = sum(QUOTA_PER_CATEGORY.values())  # 300

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_phone(p: str) -> str:
    """휴대폰 번호를 숫자만 남겨 정규화. 비교·중복 검사용 키."""
    if not p:
        return ""
    return re.sub(r"\D+", "", p)


async def _find_duplicate_identity(db, name: str, phone_norm: str, exclude_token: str | None = None) -> dict | None:
    """동일 (name, phone_normalized) 을 가진 다른 participant 가 있는지 확인."""
    if not name or not phone_norm:
        return None
    query = {"name": name, "phone_normalized": phone_norm}
    if exclude_token:
        query["token"] = {"$ne": exclude_token}
    return await db.participants.find_one(query, {"_id": 0, "token": 1, "name": 1, "email": 1})


async def _completed_count_by_category(db) -> dict[str, int]:
    """category별 사례품 동의 응답 수 (연구진·직원 테스트 제외). QUOTA_PER_CATEGORY 4개 키만 보장.
    정원의 정의: consent_reward=true 인 응답 완료자만 카운트. 미동의자는 정원 외."""
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
            "p.consent_reward": True,
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


async def _set_completion_email_state(
    db,
    *,
    token: str,
    participant: dict,
    status: str,
    error: str = "",
    is_resend: bool = False,
) -> None:
    """completion_email_states 컬렉션 단일 row를 token 기준으로 upsert.

    status: 'pending' | 'sent' | 'failed' | 'skipped'.
    'sent'/'failed' 는 시도 결과이므로 attempt_count·last_attempted_at·sent_at·last_error 갱신.
    'pending' 은 응답 제출 직후 초기화 — 기존 row가 있으면 attempt_count·last_error 보존.
    'skipped' 는 연구진처럼 발송 의무 자체가 없는 케이스 (idempotent).
    """
    now = datetime.utcnow()
    base = {
        "token": token,
        "email": participant.get("email", ""),
        "name": participant.get("name", ""),
        "org": participant.get("org", ""),
        "category": participant.get("category", ""),
        "updated_at": now,
    }
    if status == "pending":
        await db.completion_email_states.update_one(
            {"token": token},
            {
                "$set": {**base, "status": "pending"},
                "$setOnInsert": {
                    "attempt_count": 0,
                    "first_attempted_at": None,
                    "last_attempted_at": None,
                    "sent_at": None,
                    "last_error": "",
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return
    if status == "skipped":
        await db.completion_email_states.update_one(
            {"token": token},
            {
                "$set": {**base, "status": "skipped"},
                "$setOnInsert": {
                    "attempt_count": 0,
                    "first_attempted_at": None,
                    "last_attempted_at": None,
                    "sent_at": None,
                    "last_error": "",
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return
    # status in ('sent', 'failed') — 발송 시도 결과
    update = {
        "$set": {**base, "status": status, "last_attempted_at": now, "last_error": error, "is_resend": is_resend},
        "$inc": {"attempt_count": 1},
        "$setOnInsert": {"first_attempted_at": now, "created_at": now},
    }
    if status == "sent":
        update["$set"]["sent_at"] = now
    await db.completion_email_states.update_one({"token": token}, update, upsert=True)


async def _send_completion_email(participant: dict, token: str, *, is_resend: bool = False) -> None:
    """응답 제출 직후 자동 발송. 실패해도 응답 처리는 영향받지 않음.

    completion_email_states 에 시도 결과를 기록(token 단일 row, 상태 머신).
    email_logs 에는 시도별 audit row 도 그대로 남겨 추후 추적 가능.
    """
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
        "batch_id": "auto-completion-resend" if is_resend else "auto-completion",
        "token": token,
        "email": participant["email"],
        "name": participant.get("name", ""),
        "org": participant.get("org", ""),
        "category": participant.get("category", ""),
        "type": "completion",
        "subject": subject,
        "admin_email": "system",
        "admin_name": "재발송 스크립트" if is_resend else "자동 발송",
        "sent_at": now,
    }
    try:
        send_email(participant["email"], subject, html)
        log_doc.update({"status": "sent", "error": ""})
        await db.email_logs.insert_one(log_doc)
        await _set_completion_email_state(
            db, token=token, participant=participant, status="sent", is_resend=is_resend
        )
    except Exception as e:
        err = str(e)
        logger.warning(f"Completion email failed for {participant['email']}: {err}")
        log_doc.update({"status": "failed", "error": err})
        try:
            await db.email_logs.insert_one(log_doc)
        except Exception:
            pass
        try:
            await _set_completion_email_state(
                db, token=token, participant=participant, status="failed", error=err, is_resend=is_resend
            )
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

    # 토큰을 이미 보유한 응답자는 마감과 무관하게 진입 허용 — 신규 점유는 self_register에서만 차단.

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
        "needs_identity": not bool((participant.get("name") or "").strip()),
    }


@router.post("/survey/identity")
async def fill_identity(body: IdentityFillRequest, request: Request):
    """익명(name 결측) 응답자 신원 자가 보강.

    - 토큰의 participant doc 의 name 이 비어있을 때만 동작 (이미 채워진 경우는 409).
    - (name, phone_normalized) 가 다른 participant 와 중복이면 409 — 응답 1인 1회 보장.
    - 성공 시 participant doc 에 name/phone/org/phone_normalized/identity_filled_at 기록.
    """
    db = get_db()
    token = (body.token or "").strip()
    name = (body.name or "").strip()
    phone = (body.phone or "").strip()
    org = (body.org or "").strip()

    if not name:
        raise HTTPException(400, "이름을 입력해 주십시오.")
    phone_norm = _normalize_phone(phone)
    if len(phone_norm) < 9:
        raise HTTPException(400, "연락 가능한 휴대폰 번호를 입력해 주십시오.")

    participant = await db.participants.find_one({"token": token})
    if not participant:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")
    if (participant.get("name") or "").strip():
        raise HTTPException(409, "이미 신원이 등록된 토큰입니다. 다시 입력하실 수 없습니다.")

    dup = await _find_duplicate_identity(db, name, phone_norm, exclude_token=token)
    if dup:
        raise HTTPException(409, "동일한 이름·휴대폰으로 이미 등록된 응답자가 있습니다. 한 분 1회만 응답해 주십시오.")

    now = datetime.utcnow()
    update_fields = {
        "name": name,
        "phone": phone,
        "phone_normalized": phone_norm,
        "identity_filled_at": now,
        "register_ip": request.client.host if request.client else "",
        "register_ua": request.headers.get("user-agent", ""),
        "updated_at": now,
    }
    if org:
        update_fields["org"] = org
    await db.participants.update_one({"token": token}, {"$set": update_fields})
    return {"status": "ok", "token": token, "name": name}


# ── 공개 자가등록 (No Auth) ──

@router.post("/survey/register")
async def self_register(body: SelfRegisterRequest, request: Request):
    """공개 단일 링크에서 응답자가 직접 정보를 입력하고 토큰을 발급받는다.
    - email·name·category·org·consent_pi 필수.
    - 사례품 동의는 응답 시작 페이지(intro)에서 별도로 받는다 — 자가등록 단계에서 수집 안 함 (PII 분리).
    - 토큰은 random uuid. 신규 email은 새 토큰 발급.
    - imported 명단 & 미응답: 폼 입력값으로 정보 갱신 + source 전환 + 기존 토큰 노출(smooth 진입).
    - 이미 응답 완료: 차단 (재등록 의미 없음, /recover로 리뷰 링크).
    - 이미 self/staff 등록: 차단 (분실 시 /recover).
    """
    s = get_settings()

    email = (body.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "올바른 이메일을 입력해 주십시오.")
    if not body.consent_pi:
        raise HTTPException(400, "이메일 수집·이용에 동의해 주셔야 참여하실 수 있습니다.")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "이름을 입력해 주십시오.")
    if body.category not in ALLOWED_SELF_CATEGORIES:
        raise HTTPException(400, "직군(설계/시공/유지관리/건축행정)을 선택해 주십시오.")
    if not (body.org or "").strip():
        raise HTTPException(400, "소속 기관·회사명을 입력해 주십시오.")

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
    # 정원의 정의: 사례품 동의(consent_reward=true) + 응답 완료자.
    if not body.is_staff and not existing:
        if await _is_survey_closed(db):
            raise HTTPException(
                410,
                f"설문 사례품 동의 응답 정원이 모두 충족되어 신규 참여가 마감되었습니다. (4직군 합산 {SURVEY_LIMIT}부 도달)",
            )
        if await _is_category_full(db, body.category):
            raise HTTPException(
                409,
                f"'{body.category}' 직군 사례품 동의 응답 정원({QUOTA_PER_CATEGORY[body.category]}부)이 충족되어 신규 참여가 마감되었습니다.",
            )

    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

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
        "phone_normalized": "",
        "source": "staff" if body.is_staff else "self",
        "consent_pi": True,
        "consent_pi_at": now,
        # 사례품 관련 필드는 응답 시작 페이지(intro)에서 입력될 때 채워짐.
        "consent_reward": False,
        "consent_reward_at": None,
        "reward_name": "",
        "reward_phone": "",
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

    # 토큰 보유자는 마감과 무관하게 제출·수정 허용 — 신규 점유는 self_register에서만 차단.

    # 신원 게이트(/identity) 우회 방지 — 익명 토큰은 응답 제출 차단.
    if not (participant.get("name") or "").strip():
        raise HTTPException(409, "응답자 신원(이름·휴대폰) 입력이 먼저 필요합니다.")

    # (name, phone_normalized) 동일한 다른 토큰이 이미 응답 제출했으면 차단 — 1인 1회 보장 안전망.
    name = (participant.get("name") or "").strip()
    phone_norm = participant.get("phone_normalized") or _normalize_phone(participant.get("phone", ""))
    if name and phone_norm:
        dup_p = await db.participants.find_one(
            {"name": name, "phone_normalized": phone_norm, "token": {"$ne": body.token}},
            {"_id": 0, "token": 1},
        )
        if dup_p:
            dup_resp = await db.responses.find_one(
                {"token": dup_p["token"], "submitted_at": {"$ne": None}}, {"_id": 1}
            )
            if dup_resp:
                raise HTTPException(
                    409,
                    "동일한 이름·휴대폰으로 이미 응답이 제출되어 있습니다. 한 분 1회만 응답해 주십시오.",
                )

    now = datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    comments = body.comments or {}

    # 사례품 동의는 응답 시작 페이지(intro)에서 받아서 응답 제출과 함께 전송됨 — participants 갱신.
    # body.consent_reward is None 인 경우는 옛 응답 호환을 위해 변경하지 않음.
    if body.consent_reward is not None:
        reward_phone_clean = (body.reward_phone or "").strip() if body.consent_reward else ""
        phone_norm = _normalize_phone(reward_phone_clean)
        # (name, phone_normalized) 동일한 다른 토큰이 사례품 동의했다면 차단 — 1인 1회.
        if body.consent_reward and name and phone_norm:
            dup = await _find_duplicate_identity(db, name, phone_norm, exclude_token=body.token)
            if dup:
                raise HTTPException(
                    409,
                    "동일한 이름·휴대폰으로 이미 등록된 응답자가 있습니다. 한 분 1회만 응답해 주십시오.",
                )
        reward_update = {
            "consent_reward": bool(body.consent_reward),
            "consent_reward_at": now if body.consent_reward else None,
            "reward_phone": reward_phone_clean,
            "reward_name": participant.get("name", "") if body.consent_reward else "",
        }
        if body.consent_reward and phone_norm:
            reward_update["phone_normalized"] = phone_norm
        await db.participants.update_one({"token": body.token}, {"$set": reward_update})
        # 갱신된 participant doc 다시 사용 (완료 메일 발송 시 reward_name 활용 가능).
        participant = await db.participants.find_one({"token": body.token}) or participant

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
            await _set_completion_email_state(db, token=body.token, participant=participant, status="pending")
            await _send_completion_email(participant, body.token)
        else:
            await _set_completion_email_state(db, token=body.token, participant=participant, status="skipped")
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
        await _set_completion_email_state(db, token=body.token, participant=participant, status="pending")
        await _send_completion_email(participant, body.token)
    else:
        await _set_completion_email_state(db, token=body.token, participant=participant, status="skipped")
    return {"status": "created", "token": body.token}


@router.patch("/responses/{token}/reward-consent")
async def update_reward_consent(token: str, body: RewardConsentPatch):
    """이미 응답 제출한 사람이 사례품 동의·전화번호만 갱신.

    reward_notice 메일 수신자(미동의자)가 전체 응답을 다시 넘기지 않고
    intro 페이지의 [동의만 저장하고 끝내기] 버튼으로 한 번에 완료하도록 허용.
    응답 제출(`responses.submitted_at != None`) 전제 — 그 외 토큰은 409.
    """
    db = get_db()
    participant = await db.participants.find_one({"token": token})
    if not participant:
        raise HTTPException(404, "유효하지 않은 토큰입니다.")

    name = (participant.get("name") or "").strip()
    if not name:
        raise HTTPException(409, "응답자 신원이 등록되지 않은 토큰입니다.")

    submitted = await db.responses.find_one(
        {"token": token, "submitted_at": {"$ne": None}}, {"_id": 1}
    )
    if not submitted:
        raise HTTPException(
            409,
            "응답을 먼저 제출해 주십시오. 사례품 동의는 응답 제출 시 함께 저장됩니다.",
        )

    now = datetime.utcnow()

    if body.consent_reward:
        reward_phone_clean = (body.reward_phone or "").strip()
        phone_norm = _normalize_phone(reward_phone_clean)
        if len(phone_norm) < 9:
            raise HTTPException(400, "사례품 발송용 휴대폰 번호를 입력해 주십시오.")
        # 1인 1회 — (name, phone_normalized) 동일한 다른 토큰 차단
        dup = await _find_duplicate_identity(db, name, phone_norm, exclude_token=token)
        if dup:
            raise HTTPException(
                409,
                "동일한 이름·휴대폰으로 이미 등록된 응답자가 있습니다. 한 분 1회만 응답해 주십시오.",
            )
        update_fields = {
            "consent_reward": True,
            "consent_reward_at": now,
            "reward_phone": reward_phone_clean,
            "reward_name": name,
            "phone_normalized": phone_norm,
            "updated_at": now,
        }
    else:
        update_fields = {
            "consent_reward": False,
            "consent_reward_at": None,
            "reward_phone": "",
            "reward_name": "",
            "updated_at": now,
        }

    await db.participants.update_one({"token": token}, {"$set": update_fields})
    return {"status": "updated", "consent_reward": bool(body.consent_reward)}


@router.get("/responses/{token}")
async def get_response(token: str):
    db = get_db()
    doc = await db.responses.find_one({"token": token}, {"_id": 0})
    if not doc:
        return {"token": token, "responses": None}
    return doc
