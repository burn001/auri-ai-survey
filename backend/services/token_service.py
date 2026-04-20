import hmac
import hashlib


def generate_token(email: str, secret: str) -> str:
    """응답자 토큰: HMAC-SHA256 16자."""
    normalized = email.lower().strip()
    return hmac.new(
        secret.encode(), normalized.encode(), hashlib.sha256
    ).hexdigest()[:16]


def generate_admin_token(email: str, secret: str) -> str:
    """관리자 토큰: HMAC-SHA256 24자. 응답자 토큰과 길이로 구분."""
    normalized = email.lower().strip()
    return hmac.new(
        secret.encode(), ("admin:" + normalized).encode(), hashlib.sha256
    ).hexdigest()[:24]
