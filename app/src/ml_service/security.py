from hashlib import sha256
import hmac
import secrets


def hash_password(password: str) -> str:
    return sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)


def generate_auth_token() -> str:
    return secrets.token_urlsafe(32)
