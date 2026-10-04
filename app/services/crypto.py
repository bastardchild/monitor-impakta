import base64
from typing import Optional
from cryptography.fernet import Fernet
from app.config import get_settings


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        # Fallback for dev / unconfigured test environment if needed
        raise ValueError("TOKEN_ENCRYPTION_KEY is not configured in settings.")
    # If key is provided as plain text that isn't 32 url-safe base64, ensure proper length
    if isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key
    return Fernet(key_bytes)


def encrypt_token(plain_token: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext token using Fernet."""
    if not plain_token:
        return None
    f = _get_fernet()
    encrypted_bytes = f.encrypt(plain_token.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_token(cipher_token: Optional[str]) -> Optional[str]:
    """Decrypt a Fernet encrypted token."""
    if not cipher_token:
        return None
    f = _get_fernet()
    decrypted_bytes = f.decrypt(cipher_token.encode("utf-8"))
    return decrypted_bytes.decode("utf-8")
