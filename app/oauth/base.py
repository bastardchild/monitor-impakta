from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class OAuthProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Platform identifier: 'youtube', 'instagram', or 'tiktok'"""
        pass

    @abstractmethod
    def build_auth_url(self, redirect_uri: str, state: str, **kwargs) -> str:
        """Generate the authorization redirect URL."""
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str, **kwargs) -> Dict[str, Any]:
        """Exchange authorization code for tokens.
        Should return a dict with keys:
        - access_token (str)
        - refresh_token (str, optional)
        - expires_in (int, seconds)
        - token_type (str)
        - scopes (str)
        """
        pass

    @abstractmethod
    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token."""
        pass

    @abstractmethod
    async def revoke(self, token: str) -> bool:
        """Revoke authorization / token on the platform (best effort)."""
        pass

    @abstractmethod
    async def fetch_account_info(self, access_token: str) -> Dict[str, Any]:
        """Fetch account identity: external_account_id, display_name, handle, avatar_url."""
        pass
