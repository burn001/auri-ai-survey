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


class ResponseSubmit(BaseModel):
    token: str
    survey_version: str = "v7"
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
    survey_version: str = "v7"
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
