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
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ParticipantOut(BaseModel):
    token: str
    name: str
    org: str = ""
    category: str = ""
    has_responded: bool = False


class ResponseSubmit(BaseModel):
    token: str
    survey_version: str = "v7"
    responses: dict[str, Any]


class ResponseRecord(BaseModel):
    token: str
    survey_version: str
    responses: dict[str, Any]
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    ip: str = ""
    user_agent: str = ""


class StatsOut(BaseModel):
    total_participants: int
    total_responses: int
    by_category: dict[str, dict[str, int]]
