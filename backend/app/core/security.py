import hashlib
import hmac
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session_token: str) -> str:
    # The high-entropy HttpOnly cookie is the secret; only a derived token is exposed.
    return hmac.new(session_token.encode(), b"ekt-csrf-v1", hashlib.sha256).hexdigest()
