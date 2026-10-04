from typing import Dict
from app.oauth.base import OAuthProvider
from app.oauth.google import GoogleOAuthProvider
from app.oauth.meta import MetaOAuthProvider
from app.oauth.tiktok import TikTokOAuthProvider

_PROVIDERS: Dict[str, OAuthProvider] = {
    "youtube": GoogleOAuthProvider(),
    "instagram": MetaOAuthProvider(),
    "tiktok": TikTokOAuthProvider(),
}


def get_provider(platform: str) -> OAuthProvider:
    platform_key = platform.lower()
    if platform_key not in _PROVIDERS:
        raise KeyError(f"Unsupported OAuth platform: {platform}")
    return _PROVIDERS[platform_key]
