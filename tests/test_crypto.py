import logging
import pytest
from app.security import SensitiveDataFilter, mask_sensitive_data
from app.services.crypto import decrypt_token, encrypt_token


def test_encryption_decryption_cycle():
    raw_token = "ya29.a0AfH6SMD-sample-google-oauth-access-token-12345"
    encrypted = encrypt_token(raw_token)
    assert encrypted is not None
    assert encrypted != raw_token
    
    decrypted = decrypt_token(encrypted)
    assert decrypted == raw_token


def test_empty_or_none_token():
    assert encrypt_token(None) is None
    assert encrypt_token("") is None
    assert decrypt_token(None) is None
    assert decrypt_token("") is None


def test_token_masking_in_logs():
    secret_token = "super_secret_access_token_12345"
    log_message = f"Sending request with access_token: {secret_token}"
    
    masked = mask_sensitive_data(log_message)
    assert secret_token not in masked
    assert "[REDACTED]" in masked

    bearer_log = "Authorization header: Bearer ya29.a0AfH6SMD-12345"
    masked_bearer = mask_sensitive_data(bearer_log)
    assert "ya29.a0AfH6SMD-12345" not in masked_bearer


def test_sensitive_log_filter():
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="Saved client_secret='very_secret_key_abc'", args=(), exc_info=None
    )
    filter_obj = SensitiveDataFilter()
    filter_obj.filter(record)
    assert "very_secret_key_abc" not in record.msg
    assert "[REDACTED]" in record.msg
