import time
import pytest
from app.db import execute
from app.services.crypto import encrypt_token


@pytest.mark.asyncio
async def test_credentials_missing_or_invalid_api_key(async_client):
    # No header
    resp = await async_client.get("/api/internal/credentials/youtube")
    assert resp.status_code == 401

    # Wrong header
    resp = await async_client.get("/api/internal/credentials/youtube", headers={"X-API-Key": "wrong_key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_credentials_platform_not_connected_returns_409(async_client):
    # Ensure platform is disconnected
    await execute("DELETE FROM connected_accounts WHERE platform_id = 'instagram'")
    
    resp = await async_client.get(
        "/api/internal/credentials/instagram",
        headers={"X-API-Key": "test_ingest_key_valid_123"}
    )
    assert resp.status_code == 409
    data = resp.json()
    assert "not connected" in data["detail"].lower()


@pytest.mark.asyncio
async def test_credentials_platform_needs_reauth_returns_409(async_client):
    now_ts = int(time.time())
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, status, expires_at, connected_at, updated_at
        ) VALUES (
            'instagram', ?, ?, 'needs_reauth', ?, ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET status = 'needs_reauth'
        """,
        [encrypt_token("tok"), encrypt_token("ref"), now_ts + 3600, now_ts, now_ts]
    )

    resp = await async_client.get(
        "/api/internal/credentials/instagram",
        headers={"X-API-Key": "test_ingest_key_valid_123"}
    )
    assert resp.status_code == 409
    data = resp.json()
    assert "re-authentication" in data["detail"].lower()


@pytest.mark.asyncio
async def test_credentials_valid_returns_access_token(async_client):
    now_ts = int(time.time())
    token = "test_valid_access_token_instagram_999"
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, status, expires_at,
            external_account_id, display_name, connected_at, updated_at
        ) VALUES (
            'instagram', ?, ?, 'connected', ?,
            'ig_12345', 'Instagram Account', ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET
            access_token_enc = excluded.access_token_enc,
            status = 'connected',
            expires_at = excluded.expires_at,
            external_account_id = excluded.external_account_id
        """,
        [encrypt_token(token), encrypt_token("ref"), now_ts + 3600, now_ts, now_ts]
    )

    resp = await async_client.get(
        "/api/internal/credentials/instagram",
        headers={"X-API-Key": "test_ingest_key_valid_123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == token
    assert data["account_id"] == "ig_12345"
    assert data["expires_at"] == now_ts + 3600
