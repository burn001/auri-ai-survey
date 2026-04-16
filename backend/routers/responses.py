from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
from models import ResponseSubmit, ResponseRecord
from services.db import get_db

router = APIRouter(prefix="/api", tags=["responses"])


@router.get("/survey/{token}")
async def verify_token(token: str):
    db = get_db()
    participant = await db.participants.find_one(
        {"token": token}, {"_id": 0, "email": 0, "phone": 0}
    )
    if not participant:
        raise HTTPException(404, "유효하지 않은 설문 링크입니다.")

    existing = await db.responses.find_one({"token": token})
    return {
        "token": participant["token"],
        "name": participant.get("name", ""),
        "org": participant.get("org", ""),
        "category": participant.get("category", ""),
        "has_responded": existing is not None,
        "responses": existing.get("responses") if existing else None,
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

    existing = await db.responses.find_one({"token": body.token})
    if existing:
        await db.responses.update_one(
            {"token": body.token},
            {"$set": {
                "responses": body.responses,
                "survey_version": body.survey_version,
                "updated_at": now,
                "ip": ip,
                "user_agent": ua,
            }},
        )
        return {"status": "updated", "token": body.token}

    record = ResponseRecord(
        token=body.token,
        survey_version=body.survey_version,
        responses=body.responses,
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
