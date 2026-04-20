from fastapi import HTTPException, Header
from typing import Optional
from services.db import get_db


async def verify_admin_token(x_admin_token: Optional[str] = Header(None)) -> dict:
    """관리자 토큰을 검증하고 관리자 정보를 반환한다.

    - 토큰이 admins 컬렉션에 있고 active=True 상태여야 유효.
    - 반환 dict: {email, name, role, token}.
    """
    if not x_admin_token:
        raise HTTPException(401, "관리자 토큰이 필요합니다")
    db = get_db()
    admin = await db.admins.find_one(
        {"token": x_admin_token, "active": True},
        {"_id": 0, "email": 1, "name": 1, "role": 1, "token": 1},
    )
    if not admin:
        raise HTTPException(403, "관리자 인증 실패")
    return admin
