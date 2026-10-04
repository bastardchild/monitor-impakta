import urllib.parse
from typing import Any, Dict, List, Optional
import httpx
from app.config import get_settings
from app.oauth.base import OAuthProvider


class MetaOAuthProvider(OAuthProvider):
    SCOPES = [
        "instagram_basic",
        "instagram_manage_insights",
        "pages_show_list",
        "pages_read_engagement",
        "business_management",
    ]

    @property
    def name(self) -> str:
        return "instagram"

    def _graph_version(self) -> str:
        return get_settings().META_GRAPH_VERSION

    def build_auth_url(self, redirect_uri: str, state: str, **kwargs) -> str:
        settings = get_settings()
        version = self._graph_version()
        params = {
            "client_id": settings.META_APP_ID,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.SCOPES),
            "response_type": "code",
        }
        return f"https://www.facebook.com/{version}/dialog/oauth?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str, **kwargs) -> Dict[str, Any]:
        settings = get_settings()
        version = self._graph_version()
        token_endpoint = f"https://graph.facebook.com/{version}/oauth/access_token"

        # Step 1: Exchange code for short-lived user access token
        params_short = {
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        async with httpx.AsyncClient() as client:
            resp_short = await client.get(token_endpoint, params=params_short, timeout=15.0)
            resp_short.raise_for_status()
            short_token_data = resp_short.json()
            short_token = short_token_data["access_token"]

            # Step 2: Exchange short-lived token for long-lived token (~60 days)
            params_long = {
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": short_token,
            }
            resp_long = await client.get(token_endpoint, params=params_long, timeout=15.0)
            resp_long.raise_for_status()
            long_token_data = resp_long.json()

        return {
            "access_token": long_token_data["access_token"],
            "refresh_token": long_token_data["access_token"],  # Long-lived token is re-exchanged for refresh
            "expires_in": long_token_data.get("expires_in", 5184000),  # ~60 days
            "token_type": long_token_data.get("token_type", "bearer"),
            "scopes": ",".join(self.SCOPES),
        }

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        settings = get_settings()
        version = self._graph_version()
        token_endpoint = f"https://graph.facebook.com/{version}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": refresh_token,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(token_endpoint, params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()

        return {
            "access_token": data["access_token"],
            "refresh_token": data["access_token"],
            "expires_in": data.get("expires_in", 5184000),
            "token_type": data.get("token_type", "bearer"),
            "scopes": ",".join(self.SCOPES),
        }

    async def revoke(self, token: str) -> bool:
        version = self._graph_version()
        url = f"https://graph.facebook.com/{version}/me/permissions"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(url, params={"access_token": token}, timeout=10.0)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch_connected_pages(self, access_token: str) -> List[Dict[str, Any]]:
        """Fetch list of Facebook Pages with connected Instagram Business Accounts."""
        version = self._graph_version()
        url = f"https://graph.facebook.com/{version}/me/accounts"
        params = {
            "fields": "id,name,access_token,instagram_business_account{id,username,profile_picture_url}",
            "access_token": access_token,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()

        pages = data.get("data", [])
        valid_pages = []
        for p in pages:
            ig_account = p.get("instagram_business_account")
            if ig_account:
                valid_pages.append({
                    "page_id": p.get("id"),
                    "page_name": p.get("name"),
                    "ig_id": ig_account.get("id"),
                    "ig_username": ig_account.get("username"),
                    "profile_picture_url": ig_account.get("profile_picture_url", ""),
                })
        return valid_pages

    async def fetch_account_info(self, access_token: str, ig_account_id: Optional[str] = None) -> Dict[str, Any]:
        pages = await self.fetch_connected_pages(access_token)
        if not pages:
            raise ValueError(
                "Tidak ditemukan akun Instagram Business/Creator yang terhubung ke Facebook Page. "
                "Pastikan akun Instagram Anda bertipe Profesional (Business/Creator) dan sudah ditautkan ke Facebook Page."
            )

        selected = None
        if ig_account_id:
            for p in pages:
                if p["ig_id"] == ig_account_id:
                    selected = p
                    break
        if not selected:
            selected = pages[0]

        return {
            "external_account_id": selected["ig_id"],
            "display_name": selected["ig_username"],
            "handle": f"@{selected['ig_username']}",
            "avatar_url": selected.get("profile_picture_url", ""),
            "all_pages": pages,
        }
