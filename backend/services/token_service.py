import hmac
import hashlib


def generate_token(email: str, secret: str) -> str:
    normalized = email.lower().strip()
    return hmac.new(
        secret.encode(), normalized.encode(), hashlib.sha256
    ).hexdigest()[:16]
