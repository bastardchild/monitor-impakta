import pytest
import respx
import httpx
from app.db import fetch_one
from app.services.crypto import decrypt_token


@pytest.mark.asyncio
async def test_oauth_connect_redirect(async_client):
    from app.security import hash_password
    import app.config as config
    settings = config.get_settings()
    settings.ADMIN_PASSWORD_HASH = hash_password("testpass")

    login_resp = await async_client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
    assert login_resp.status_code == 303
    async_client.cookies.update(login_resp.cookies)

    # Access /connect/youtube
    resp = await async_client.get("/connect/youtube", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "accounts.google.com/o/oauth2/v2/auth" in location
    assert "state=" in location


@pytest.mark.asyncio
async def test_oauth_state_mismatch_csrf(async_client):
    from app.security import hash_password
    import app.config as config
    settings = config.get_settings()
    settings.ADMIN_PASSWORD_HASH = hash_password("testpass")

    login_resp = await async_client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
    async_client.cookies.update(login_resp.cookies)

    # Initiate /connect/youtube to get a real state stored in session
    init_resp = await async_client.get("/connect/youtube", follow_redirects=False)
    async_client.cookies.update(init_resp.cookies)

    # Attempt callback with different state
    callback_resp = await async_client.get(
        "/connect/youtube/callback?code=mock_code_123&state=tampered_fraudulent_state",
        follow_redirects=False
    )
    # Should redirect with flash error
    assert callback_resp.status_code == 303
    assert callback_resp.headers["location"] == "/settings/connections"


@pytest.mark.asyncio
@respx.mock
async def test_google_youtube_callback_success(async_client):
    from app.security import hash_password
    import app.config as config
    settings = config.get_settings()
    settings.ADMIN_PASSWORD_HASH = hash_password("testpass")
    settings.GOOGLE_CLIENT_ID = "mock_client_id"
    settings.GOOGLE_CLIENT_SECRET = "mock_client_secret"

    login_resp = await async_client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
    async_client.cookies.update(login_resp.cookies)

    # 1. Initiate to populate session state
    init_resp = await async_client.get("/connect/youtube", follow_redirects=False)
    location = init_resp.headers["location"]
    state = location.split("state=")[1].split("&")[0]
    async_client.cookies.update(init_resp.cookies)

    # 2. Mock Google endpoints
    respx.post("https://oauth2.googleapis.com/token").respond(
        status_code=200,
        json={
            "access_token": "ya29.mock_access_token_123",
            "refresh_token": "1//mock_refresh_token_abc",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/youtube.readonly",
        }
    )
    respx.get("https://www.googleapis.com/youtube/v3/channels").respond(
        status_code=200,
        json={
            "items": [
                {
                    "id": "UC_YOUTUBE_TEST_CHANNEL",
                    "snippet": {
                        "title": "Channel Test Studio",
                        "customUrl": "@ChannelTestStudio",
                        "thumbnails": {
                            "default": {"url": "https://example.com/yt-avatar.jpg"}
                        }
                    }
                }
            ]
        }
    )

    # 3. Call callback with matching state
    cb_resp = await async_client.get(
        f"/connect/youtube/callback?code=valid_test_code&state={state}",
        follow_redirects=False
    )
    assert cb_resp.status_code == 303
    assert cb_resp.headers["location"] == "/settings/connections"

    # Verify database record
    account = await fetch_one("SELECT * FROM connected_accounts WHERE platform_id = 'youtube'")
    assert account is not None
    assert account["status"] == "connected"
    assert account["display_name"] == "Channel Test Studio"
    assert decrypt_token(account["access_token_enc"]) == "ya29.mock_access_token_123"
    assert decrypt_token(account["refresh_token_enc"]) == "1//mock_refresh_token_abc"


@pytest.mark.asyncio
@respx.mock
async def test_tiktok_callback_success(async_client):
    from app.security import hash_password
    import app.config as config
    settings = config.get_settings()
    settings.ADMIN_PASSWORD_HASH = hash_password("testpass")
    settings.TIKTOK_CLIENT_KEY = "mock_tt_key"
    settings.TIKTOK_CLIENT_SECRET = "mock_tt_secret"

    login_resp = await async_client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
    async_client.cookies.update(login_resp.cookies)

    # Initiate
    init_resp = await async_client.get("/connect/tiktok", follow_redirects=False)
    location = init_resp.headers["location"]
    state = location.split("state=")[1].split("&")[0]
    async_client.cookies.update(init_resp.cookies)

    # Mock TikTok Token & User Info
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").respond(
        status_code=200,
        json={
            "data": {
                "access_token": "act.mock_tiktok_token_xyz",
                "refresh_token": "rft.mock_tiktok_refresh_xyz",
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
                "token_type": "Bearer",
                "scope": "user.info.basic,user.info.stats,video.list",
                "open_id": "open_id_tiktok_123"
            }
        }
    )
    respx.get("https://open.tiktokapis.com/v2/user/info/").respond(
        status_code=200,
        json={
            "data": {
                "user": {
                    "open_id": "open_id_tiktok_123",
                    "display_name": "TikTok Creator Demo",
                    "username": "ttcreatordemo",
                    "avatar_url": "https://example.com/tiktok-avatar.jpg"
                }
            }
        }
    )

    cb_resp = await async_client.get(
        f"/connect/tiktok/callback?code=mock_tiktok_code&state={state}",
        follow_redirects=False
    )
    assert cb_resp.status_code == 303
    assert cb_resp.headers["location"] == "/settings/connections"

    account = await fetch_one("SELECT * FROM connected_accounts WHERE platform_id = 'tiktok'")
    assert account is not None
    assert account["status"] == "connected"
    assert account["display_name"] == "TikTok Creator Demo"
    assert decrypt_token(account["access_token_enc"]) == "act.mock_tiktok_token_xyz"
    assert decrypt_token(account["refresh_token_enc"]) == "rft.mock_tiktok_refresh_xyz"
