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


class SelfRegisterRequest(BaseModel):
    """공개 단일 링크 자가등록 페이로드.

    필수: email(완료 메일 발송용) + 직군(category) + 소속(org).
    선택: 부서·직위·직급·담당업무 — 통계 분석 보조 정보.
    사례품(consent_reward) 동의 시에만 reward_name·reward_phone 수집.
    """
    email: str                     # 필수 — 응답 완료 안내 메일 발송용
    org: str = ""                  # 소속 기관·회사명
    category: str                  # "설계" | "시공" | "유지관리" | "건축행정"
    dept: str = ""
    team: str = ""
    position: str = ""
    rank: str = ""
    duty: str = ""
    consent_pi: bool                  # 필수동의 — 이메일 수집·이용
    consent_reward: bool = False      # 선택동의 — true면 reward_name/reward_phone 필요
    reward_name: str = ""             # 사례품 동의 시 수령자명
    reward_phone: str = ""             # 사례품 동의 시 휴대폰 번호
    is_staff: bool = False             # 직원 테스트 모드 — source='staff', 정원·분석 제외


class ResponseSubmit(BaseModel):
    token: str
    survey_version: str = "v11"
    responses: dict[str, Any]
    comments: Optional[dict[str, str]] = None  # reviewer (연구진) only


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
