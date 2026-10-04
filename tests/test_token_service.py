import time
import pytest
import respx
from app.db import execute, fetch_one
from app.services.crypto import decrypt_token, encrypt_token
from app.services.token_service import (
    TokenServiceError,
    get_valid_credentials,
    refresh_platform_token,
)


@pytest.mark.asyncio
@respx.mock
async def test_tiktok_token_rotation_on_refresh():
    now_ts = int(time.time())
    old_access = "old_tt_access"
    old_refresh = "old_tt_refresh"
    new_access = "brand_new_tt_access"
    new_refresh = "rotated_brand_new_tt_refresh"

    # Setup database with existing connected TikTok account
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, token_type, scopes,
            expires_at, refresh_expires_at, external_account_id, display_name,
            avatar_url, status, last_refresh_at, connected_at, updated_at
        ) VALUES (
            'tiktok', ?, ?, 'Bearer', 'read',
            ?, ?, 'tt_user_1', 'TT User', '', 'connected', ?, ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET
            access_token_enc = excluded.access_token_enc,
            refresh_token_enc = excluded.refresh_token_enc,
            expires_at = excluded.expires_at,
            status = 'connected'
        """,
        [encrypt_token(old_access), encrypt_token(old_refresh), now_ts + 60, now_ts + 86400, now_ts, now_ts, now_ts]
    )

    # Mock TikTok refresh endpoint returning a rotated refresh token
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").respond(
        status_code=200,
        json={
            "data": {
                "access_token": new_access,
                "refresh_token": new_refresh,
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
                "token_type": "Bearer",
                "scope": "user.info.basic",
            }
        }
    )

    result = await refresh_platform_token("tiktok")
    assert result["access_token"] == new_access

    # Verify that the NEW rotated refresh token was persisted in Turso
    account = await fetch_one("SELECT * FROM connected_accounts WHERE platform_id = 'tiktok'")
    assert decrypt_token(account["access_token_enc"]) == new_access
    assert decrypt_token(account["refresh_token_enc"]) == new_refresh


@pytest.mark.asyncio
@respx.mock
async def test_refresh_failure_marks_needs_reauth():
    now_ts = int(time.time())
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, status, expires_at, connected_at, updated_at
        ) VALUES (
            'youtube', ?, ?, 'connected', ?, ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET
            refresh_token_enc = excluded.refresh_token_enc,
            status = 'connected'
        """,
        [encrypt_token("some_access"), encrypt_token("some_refresh"), now_ts - 100, now_ts, now_ts]
    )

    # Mock failure on Google token refresh endpoint
    respx.post("https://oauth2.googleapis.com/token").respond(
        status_code=400,
        json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
    )

    with pytest.raises(TokenServiceError) as exc_info:
        await refresh_platform_token("youtube", max_retries=2)
    assert exc_info.value.status_code == 409

    account = await fetch_one("SELECT status, last_error FROM connected_accounts WHERE platform_id = 'youtube'")
    assert account["status"] == "needs_reauth"
    assert "Refresh failed" in account["last_error"]


@pytest.mark.asyncio
@respx.mock
async def test_lazy_refresh_in_get_valid_credentials():
    now_ts = int(time.time())
    # Expiring in 200 seconds (< 600 seconds threshold)
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, status, expires_at, external_account_id, display_name, connected_at, updated_at
        ) VALUES (
            'youtube', ?, ?, 'connected', ?, 'ext_yt', 'YT Title', ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET
            access_token_enc = excluded.access_token_enc,
            refresh_token_enc = excluded.refresh_token_enc,
            expires_at = excluded.expires_at,
            status = 'connected'
        """,
        [encrypt_token("expiring_access"), encrypt_token("valid_refresh"), now_ts + 200, now_ts, now_ts]
    )

    respx.post("https://oauth2.googleapis.com/token").respond(
        status_code=200,
        json={
            "access_token": "freshly_refreshed_access",
            "refresh_token": "valid_refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )

    creds = await get_valid_credentials("youtube")
    assert creds["access_token"] == "freshly_refreshed_access"
    assert creds["expires_at"] > now_ts + 3000
