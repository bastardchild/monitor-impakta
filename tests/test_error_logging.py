import pytest
from datetime import datetime, timezone, timedelta
from app.db import execute, fetch_one
from app.services.error_logger import (
    sanitize_data,
    log_system_error,
    get_recent_error_logs,
    prune_old_error_logs,
    ErrorCode,
    ErrorCategory,
)


def test_sanitize_data_masks_sensitive_information():
    data = {
        "access_token": "ya29.a0AfH6SMD_very_secret_google_oauth_token",
        "refresh_token": "1//0eXYZ_refresh_secret",
        "client_secret": "my_super_secret_client_key",
        "password": "supersecretpassword123",
        "nested": {
            "token": "tok_1234567890",
            "api_key": "sec_key_xyz",
            "user_id": 42,
            "status": "active",
        },
        "headers": {
            "authorization": "Bearer ya29.secret_bearer_token_value",
            "content-type": "application/json",
        },
        "debug_log": "Failed with header Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
    }

    sanitized = sanitize_data(data)

    # Assert masked fields
    assert sanitized["access_token"] == "[MASKED_SECRET]"
    assert sanitized["refresh_token"] == "[MASKED_SECRET]"
    assert sanitized["client_secret"] == "[MASKED_SECRET]"
    assert sanitized["password"] == "[MASKED_SECRET]"
    assert sanitized["nested"]["token"] == "[MASKED_SECRET]"
    assert sanitized["nested"]["api_key"] == "[MASKED_SECRET]"
    assert sanitized["headers"]["authorization"] == "[MASKED_SECRET]"

    # Assert non-sensitive fields preserved
    assert sanitized["nested"]["user_id"] == 42
    assert sanitized["nested"]["status"] == "active"
    assert sanitized["headers"]["content-type"] == "application/json"
    assert "Bearer [MASKED_TOKEN]" in sanitized["debug_log"]


@pytest.mark.asyncio
async def test_log_system_error_persists_sanitized_record():
    err_context = {
        "access_token": "raw_sensitive_token_leak",
        "platform": "youtube",
        "attempt": 3,
    }

    error_id = await log_system_error(
        category=ErrorCategory.INGEST,
        error_code=ErrorCode.INGEST_PAYLOAD_INVALID,
        message="Invalid payload structure during ingestion",
        path="/api/ingest/account-metrics",
        method="POST",
        platform_id="youtube",
        status_code=400,
        context=err_context,
    )

    assert error_id.startswith("err_")

    # Fetch from database
    row = await fetch_one("SELECT * FROM system_error_logs WHERE id = ?", [error_id])
    assert row is not None
    assert row["error_code"] == ErrorCode.INGEST_PAYLOAD_INVALID
    assert row["category"] == ErrorCategory.INGEST
    assert row["status_code"] == 400
    assert row["platform_id"] == "youtube"
    assert row["path"] == "/api/ingest/account-metrics"

    # Verify context details in DB were sanitized
    assert "raw_sensitive_token_leak" not in row["details_json"]
    assert "[MASKED_SECRET]" in row["details_json"]


@pytest.mark.asyncio
async def test_get_recent_error_logs_filtering():
    # Insert multiple test logs
    await log_system_error(
        category=ErrorCategory.OAUTH,
        error_code=ErrorCode.OAUTH_STATE_INVALID,
        message="OAuth state mismatch test",
        platform_id="instagram",
    )
    await log_system_error(
        category=ErrorCategory.DATABASE,
        error_code=ErrorCode.DATABASE_QUERY_ERROR,
        message="DB connection error test",
        platform_id=None,
    )

    # Query all
    all_logs = await get_recent_error_logs(limit=20)
    assert len(all_logs) >= 2

    # Query filtered by category
    oauth_logs = await get_recent_error_logs(category=ErrorCategory.OAUTH)
    assert any(log["error_code"] == ErrorCode.OAUTH_STATE_INVALID for log in oauth_logs)
    assert not any(log["category"] == ErrorCategory.DATABASE for log in oauth_logs)

    # Verify formatting added
    for log in oauth_logs:
        assert "created_at_formatted" in log
        assert "badge_class" in log
        assert isinstance(log["details"], dict)


@pytest.mark.asyncio
async def test_prune_old_error_logs():
    # Insert an old log with created_at 45 days ago
    old_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    old_id = "err_test_old_to_be_pruned"
    await execute(
        """
        INSERT INTO system_error_logs (
            id, error_code, category, message, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [old_id, ErrorCode.INTERNAL_SERVER_ERROR, ErrorCategory.SYSTEM, "Old message to prune", old_time]
    )

    # Insert a fresh log
    fresh_time = datetime.now(timezone.utc).isoformat()
    fresh_id = "err_test_fresh_to_keep"
    await execute(
        """
        INSERT INTO system_error_logs (
            id, error_code, category, message, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [fresh_id, ErrorCode.INTERNAL_SERVER_ERROR, ErrorCategory.SYSTEM, "Fresh message", fresh_time]
    )

    # Prune logs older than 30 days
    pruned_count = await prune_old_error_logs(retention_days=30)
    assert pruned_count >= 1

    # Verify old log is removed, fresh log remains
    old_row = await fetch_one("SELECT * FROM system_error_logs WHERE id = ?", [old_id])
    assert old_row is None

    fresh_row = await fetch_one("SELECT * FROM system_error_logs WHERE id = ?", [fresh_id])
    assert fresh_row is not None


@pytest.mark.asyncio
async def test_fastapi_request_validation_error_handler(async_client):
    # Missing required fields on POST /api/ingest/account-metrics
    resp = await async_client.post(
        "/api/ingest/account-metrics",
        json={"invalid_key": "some_value"},
        headers={"X-API-KEY": "test_ingest_key_valid_123"},
    )

    assert resp.status_code == 422
    data = resp.json()
    assert data["status"] == "error"
    assert data["error_code"] == ErrorCode.VALIDATION_ERROR
    assert "error_id" in data
    assert resp.headers.get("x-correlation-id") == data["error_id"]


@pytest.mark.asyncio
async def test_error_logs_ui_and_partial_rendering(async_client):
    from app.security import hash_password
    import app.config as config
    settings = config.get_settings()
    settings.ADMIN_PASSWORD_HASH = hash_password("testpass")

    # Login as admin
    login_resp = await async_client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
    assert login_resp.status_code == 303
    async_client.cookies.update(login_resp.cookies)

    # 1. Test /settings/logs full page
    page_resp = await async_client.get("/settings/logs")
    assert page_resp.status_code == 200
    assert "Log Sistem" in page_resp.text
    assert "Correlation ID" in page_resp.text
    assert "error-logs-container" in page_resp.text

    # 2. Insert a test log and test /partials/error-logs
    test_id = await log_system_error(
        category=ErrorCategory.AUTH,
        error_code=ErrorCode.CREDENTIALS_NOT_FOUND,
        message="Test UI error log entry",
        path="/settings/logs",
        status_code=404,
    )

    partial_resp = await async_client.get("/partials/error-logs?category=all")
    assert partial_resp.status_code == 200
    assert test_id in partial_resp.text
    assert "Test UI error log entry" in partial_resp.text
    assert "Detail Error Event" in partial_resp.text

