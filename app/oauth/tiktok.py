import urllib.parse
from typing import Any, Dict
import httpx
from app.config import get_settings
from app.oauth.base import OAuthProvider


class TikTokOAuthProvider(OAuthProvider):
    AUTH_ENDPOINT = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_ENDPOINT = "https://open.tiktokapis.com/v2/oauth/token/"
    REVOKE_ENDPOINT = "https://open.tiktokapis.com/v2/oauth/revoke/"
    USER_INFO_ENDPOINT = "https://open.tiktokapis.com/v2/user/info/"
    
    SCOPES = [
        "user.info.basic",
        "user.info.profile",
        "user.info.stats",
        "video.list",
    ]

    @property
    def name(self) -> str:
        return "tiktok"

    def build_auth_url(self, redirect_uri: str, state: str, **kwargs) -> str:
        settings = get_settings()
        params = {
            "client_key": settings.TIKTOK_CLIENT_KEY,
            "scope": ",".join(self.SCOPES),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str, **kwargs) -> Dict[str, Any]:
        settings = get_settings()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "client_key": settings.TIKTOK_CLIENT_KEY,
            "client_secret": settings.TIKTOK_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=data, headers=headers, timeout=15.0)
            resp.raise_for_status()
            res_json = resp.json()

        token_data = res_json.get("data", res_json)
        if "error" in res_json and res_json["error"].get("code") != "ok" and res_json["error"].get("code") != 0:
            raise ValueError(f"TikTok OAuth Error: {res_json['error']}")

        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in", 86400),
            "refresh_expires_in": token_data.get("refresh_expires_in", 31536000),
            "token_type": token_data.get("token_type", "Bearer"),
            "scopes": token_data.get("scope", ",".join(self.SCOPES)),
            "open_id": token_data.get("open_id"),
        }

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        settings = get_settings()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "client_key": settings.TIKTOK_CLIENT_KEY,
            "client_secret": settings.TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=data, headers=headers, timeout=15.0)
            resp.raise_for_status()
            res_json = resp.json()

        token_data = res_json.get("data", res_json)
        if "error" in res_json and res_json["error"].get("code") != "ok" and res_json["error"].get("code") != 0:
            raise ValueError(f"TikTok OAuth Refresh Error: {res_json['error']}")

        # IMPORTANT: TikTok may return a new refresh token. Always persist it.
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", refresh_token),
            "expires_in": token_data.get("expires_in", 86400),
            "refresh_expires_in": token_data.get("refresh_expires_in", 31536000),
            "token_type": token_data.get("token_type", "Bearer"),
            "scopes": token_data.get("scope", ",".join(self.SCOPES)),
            "open_id": token_data.get("open_id"),
        }

    async def revoke(self, token: str) -> bool:
        settings = get_settings()
        data = {
            "client_key": settings.TIKTOK_CLIENT_KEY,
            "client_secret": settings.TIKTOK_CLIENT_SECRET,
            "token": token,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.REVOKE_ENDPOINT, data=data, timeout=10.0)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_account_info(self, access_token: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        # Coba minta fields lengkap: open_id, union_id, avatar_url, display_name, username
        params = {"fields": "open_id,display_name,username,avatar_url"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.USER_INFO_ENDPOINT, headers=headers, params=params, timeout=15.0)
            
            # Jika 401/400 kemungkinan karena scope user.info.profile belum disetujui, coba fallback hanya basic fields
            if resp.status_code in (400, 401, 403):
                fallback_params = {"fields": "open_id,avatar_url"}
                resp_fallback = await client.get(self.USER_INFO_ENDPOINT, headers=headers, params=fallback_params, timeout=15.0)
                if resp_fallback.status_code == 200:
                    resp = resp_fallback
                else:
                    # Log detail error body dari TikTok
                    err_body = resp.text
                    raise ValueError(f"TikTok user info API rejected ({resp.status_code}): {err_body}")
            else:
                resp.raise_for_status()
                
            res_json = resp.json()

        user_data = res_json.get("data", {}).get("user", res_json.get("user", {}))
        if not user_data:
            raise ValueError(f"Unable to retrieve TikTok user profile from response: {res_json}")

        open_id = user_data.get("open_id", "")
        username = user_data.get("username", "")
        display_name = user_data.get("display_name", username or "Akun TikTok")
        avatar_url = user_data.get("avatar_url", "")

        return {
            "external_account_id": open_id,
            "display_name": display_name,
            "handle": f"@{username}" if username else display_name,
            "avatar_url": avatar_url,
        }
