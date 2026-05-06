from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime


class Participant(BaseModel):
    token: str
    email: str
    name: str
    org: str = ""
    category: str = ""
    field: str = ""
    phone: str = ""
    dept: str = ""
    team: str = ""
    position: str = ""
    rank: str = ""
    duty: str = ""
    # 등록 출처: imported(엑셀 import) | self(공개 링크 자가등록)
    source: str = "imported"
    # 개인정보 동의 (자가등록 시 필수동의·선택동의 분리 기록)
    consent_pi: bool = False
    consent_pi_at: Optional[datetime] = None
    consent_reward: bool = False
    consent_reward_at: Optional[datetime] = None
    # 사례품(스타벅스 e카드) 수령 정보 — 선택동의 시에만 채워짐
    reward_name: str = ""
    reward_phone: str = ""
    # 자가등록 추적 메타
    register_ip: str = ""
    register_ua: str = ""
    register_updated_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ParticipantOut(BaseModel):
    token: str
    name: str
    org: str = ""
    category: str = ""
    has_responded: bool = False


class ParticipantUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    org: Optional[str] = None
    phone: Optional[str] = None
    dept: Optional[str] = None
    team: Optional[str] = None
    position: Optional[str] = None
    rank: Optional[str] = None
    duty: Optional[str] = None
    reward_name: Optional[str] = None
    reward_phone: Optional[str] = None


class RecoverRequest(BaseModel):
    """기존 자가등록자가 토큰을 분실한 경우 — email로 본인 토큰 링크 재발송."""
    email: str


class IdentityFillRequest(BaseModel):
    """익명(name 결측) 응답자가 응답 시작 시 신원을 자가 보강.

    name 비어있는 participant doc 한정. (name, phone) 중복 검사 후 업데이트.
    bulk_import_2026_05_04 로 들어온 5,321건의 FM학회 회원·법인회원사 등 익명 토큰을 위한 게이트.
    """
    token: str
    name: str
    phone: str
    org: str = ""


class SelfRegisterRequest(BaseModel):
    """공개 단일 링크 자가등록 페이로드.

    필수: email(완료 메일 발송용) + name + 직군(category) + 소속(org) + consent_pi.
    선택: 부서·직위·직급·담당업무 — 통계 분석 보조 정보.
    사례품 동의는 응답 시작 페이지(intro)에서 별도로 받는다 — 자가등록 단계에선 수집 안 함.
    """
    email: str                     # 필수 — 응답 완료 안내 메일 발송용
    name: str = ""                 # 응답자 이름 (1인 1회 보장 키 + 사례품 안내)
    org: str = ""                  # 소속 기관·회사명
    category: str                  # "설계" | "시공" | "유지관리" | "건축행정"
    dept: str = ""
    team: str = ""
    position: str = ""
    rank: str = ""
    duty: str = ""
    consent_pi: bool                  # 필수동의 — 이메일 수집·이용
    is_staff: bool = False             # 직원 테스트 모드 — source='staff', 정원·분석 제외


class ResponseSubmit(BaseModel):
    token: str
    survey_version: str = "v13"
    responses: dict[str, Any]
    comments: Optional[dict[str, str]] = None  # reviewer (연구진) only
    # 사례품 동의는 응답 시작 페이지(intro)에서 받는다. PII 분리 위해 응답 dict 와 별도 필드.
    consent_reward: Optional[bool] = None  # None = 옛 응답 호환 (변경 없음)
    reward_phone: Optional[str] = None


class RewardConsentPatch(BaseModel):
    """이미 응답 제출한 미동의자가 사례품 동의·연락처만 갱신할 때 사용.

    reward_notice 메일 수신자가 전체 응답을 다시 넘기지 않고 한 번에 동의 정보만
    저장하도록 허용. consent_reward=True 면 reward_phone 필수.
    """
    consent_reward: bool
    reward_phone: Optional[str] = None


class ResponseRecord(BaseModel):
    token: str
    survey_version: str
    responses: dict[str, Any]
    comments: dict[str, str] = Field(default_factory=dict)
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    ip: str = ""
    user_agent: str = ""


class StatsOut(BaseModel):
    total_participants: int
    total_responses: int
    by_category: dict[str, dict[str, int]]


class EmailLog(BaseModel):
    """1 row per email send attempt (success or failure)."""
    batch_id: str
    token: str
    email: str
    name: str = ""
    org: str = ""
    category: str = ""
    type: str = "invite"  # invite | reminder | deadline | custom
    subject: str
    status: str  # sent | failed
    error: str = ""
    admin_email: str = ""
    admin_name: str = ""
    sent_at: datetime = Field(default_factory=datetime.utcnow)


# ── Review Comments (스레드 기반 검토 코멘트) ──
# survey_version 단위로 공유되는 별도 컬렉션. 모든 연구진/관리자가 서로의 코멘트를 본다.

COMMENT_STATUSES = {"open", "in_review", "resolved", "rejected"}
COMMENT_ROLES = {"reviewer", "admin"}


class ReviewComment(BaseModel):
    id: str  # uuid hex
    survey_version: str = "v11"
    qid: str  # 문항 ID
    author_role: str  # reviewer | admin
    author_token: str  # respondent or admin token (소유권 확인용)
    author_name: str = ""
    author_email: str = ""
    author_org: str = ""
    text: str
    status: str = "open"  # open | in_review | resolved | rejected
    parent_id: Optional[str] = None  # 답글일 때 부모 entry id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    status_changed_at: Optional[datetime] = None
    status_changed_by: str = ""  # 상태 변경자 이름


class CommentCreateRequest(BaseModel):
    text: str
    parent_id: Optional[str] = None


class CommentUpdateRequest(BaseModel):
    text: Optional[str] = None
    status: Optional[str] = None  # 관리자만 변경 가능
