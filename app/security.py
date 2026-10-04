import secrets
import logging
import re
import bcrypt
from typing import Optional

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its bcrypt hash."""
    if not plain_password or not hashed_password:
        return False
    if "$$" in hashed_password:
        hashed_password = hashed_password.replace("$$", "$")
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception as e:
        logger.warning(f"Password verification error: {e}")
        return False


def verify_api_key(provided_key: Optional[str], expected_key: Optional[str]) -> bool:
    """Constant-time comparison for API keys."""
    if not provided_key or not expected_key:
        return False
    return secrets.compare_digest(provided_key, expected_key)


def generate_oauth_state() -> str:
    """Generate a cryptographically secure state token for OAuth."""
    return secrets.token_urlsafe(32)


# Logging Sanitizer
SENSITIVE_PATTERNS = [
    re.compile(r'(access_token|refresh_token|token|secret|authorization|password|api_key|client_secret)["\']?\s*[:=]\s*["\']?([^"\'\s&]+)', re.IGNORECASE),
    re.compile(r'(Bearer\s+)[A-Za-z0-9_\-\.]+', re.IGNORECASE),
]


def mask_sensitive_data(text: str) -> str:
    """Mask sensitive credentials from strings for safe logging."""
    if not isinstance(text, str):
        return text
    
    sanitized = text
    for pattern in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(r'\1: [REDACTED]', sanitized)
    return sanitized


class SensitiveDataFilter(logging.Filter):
    """Logging filter to mask sensitive values in all log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_sensitive_data(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (mask_sensitive_data(str(v)) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_sensitive_data(str(v)) if isinstance(v, str) else v for v in record.args)
        return True
