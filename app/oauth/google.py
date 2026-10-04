import urllib.parse
from typing import Any, Dict
import httpx
from app.config import get_settings
from app.oauth.base import OAuthProvider


class GoogleOAuthProvider(OAuthProvider):
    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
    CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"
    
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    @property
    def name(self) -> str:
        return "youtube"

    def build_auth_url(self, redirect_uri: str, state: str, **kwargs) -> str:
        settings = get_settings()
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str, **kwargs) -> Dict[str, Any]:
        settings = get_settings()
        data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=data, timeout=15.0)
            resp.raise_for_status()
            res_json = resp.json()

        return {
            "access_token": res_json["access_token"],
            "refresh_token": res_json.get("refresh_token"),
            "expires_in": res_json.get("expires_in", 3600),
            "token_type": res_json.get("token_type", "Bearer"),
            "scopes": res_json.get("scope", " ".join(self.SCOPES)),
        }

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        settings = get_settings()
        data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=data, timeout=15.0)
            resp.raise_for_status()
            res_json = resp.json()

        return {
            "access_token": res_json["access_token"],
            "refresh_token": res_json.get("refresh_token", refresh_token),
            "expires_in": res_json.get("expires_in", 3600),
            "token_type": res_json.get("token_type", "Bearer"),
            "scopes": res_json.get("scope", " ".join(self.SCOPES)),
        }

    async def revoke(self, token: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.REVOKE_ENDPOINT, params={"token": token}, timeout=10.0)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_account_info(self, access_token: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"part": "id,snippet", "mine": "true"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.CHANNELS_ENDPOINT, headers=headers, params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        if not items:
            raise ValueError("No YouTube channel found for this Google account.")

        item = items[0]
        snippet = item.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        default_thumb = thumbnails.get("default", {}).get("url") or thumbnails.get("medium", {}).get("url", "")
        
        return {
            "external_account_id": item["id"],
            "display_name": snippet.get("title", ""),
            "handle": snippet.get("customUrl", snippet.get("title", "")),
            "avatar_url": default_thumb,
        }
