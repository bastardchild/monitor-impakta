import asyncio
import logging
import time
from typing import Any, Dict, Optional
from app.db import fetch_one, execute
from app.oauth import get_provider
from app.services.crypto import encrypt_token, decrypt_token
from app.services.error_logger import log_system_error, ErrorCode, ErrorCategory

logger = logging.getLogger(__name__)

# Platform locks to prevent race conditions during token refresh
_PLATFORM_LOCKS: Dict[str, asyncio.Lock] = {
    "youtube": asyncio.Lock(),
    "instagram": asyncio.Lock(),
    "tiktok": asyncio.Lock(),
}


def get_platform_lock(platform: str) -> asyncio.Lock:
    if platform not in _PLATFORM_LOCKS:
        _PLATFORM_LOCKS[platform] = asyncio.Lock()
    return _PLATFORM_LOCKS[platform]


class TokenServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def save_connected_account(
    platform: str,
    tokens: Dict[str, Any],
    account_info: Dict[str, Any],
) -> None:
    """Encrypt and persist connected account information into database."""
    now_ts = int(time.time())
    expires_at = now_ts + int(tokens.get("expires_in", 3600))
    refresh_expires_in = tokens.get("refresh_expires_in")
    refresh_expires_at = (now_ts + int(refresh_expires_in)) if refresh_expires_in else None

    access_token_enc = encrypt_token(tokens.get("access_token"))
    refresh_token_enc = encrypt_token(tokens.get("refresh_token"))

    # Upsert platform handle & account_id
    await execute(
        """
        UPDATE platforms 
        SET handle = ?, account_id = ?
        WHERE id = ?
        """,
        [account_info.get("handle", ""), account_info.get("external_account_id", ""), platform]
    )

    # Upsert connected_accounts
    await execute(
        """
        INSERT INTO connected_accounts (
            platform_id, access_token_enc, refresh_token_enc, token_type, scopes,
            expires_at, refresh_expires_at, external_account_id, display_name,
            avatar_url, status, last_refresh_at, last_error, connected_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, 'connected', ?, NULL, ?, ?
        )
        ON CONFLICT(platform_id) DO UPDATE SET
            access_token_enc = excluded.access_token_enc,
            refresh_token_enc = excluded.refresh_token_enc,
            token_type = excluded.token_type,
            scopes = excluded.scopes,
            expires_at = excluded.expires_at,
            refresh_expires_at = excluded.refresh_expires_at,
            external_account_id = excluded.external_account_id,
            display_name = excluded.display_name,
            avatar_url = excluded.avatar_url,
            status = 'connected',
            last_refresh_at = excluded.last_refresh_at,
            last_error = NULL,
            updated_at = excluded.updated_at
        """,
        [
            platform, access_token_enc, refresh_token_enc, tokens.get("token_type", "Bearer"),
            tokens.get("scopes", ""), expires_at, refresh_expires_at,
            account_info.get("external_account_id", ""), account_info.get("display_name", ""),
            account_info.get("avatar_url", ""), now_ts, now_ts, now_ts
        ]
    )
    logger.info(f"Connected account for platform '{platform}' saved successfully.")


async def refresh_platform_token(platform: str, max_retries: int = 3) -> Dict[str, Any]:
    """Refresh token with retry, backoff, and lock."""
    lock = get_platform_lock(platform)
    async with lock:
        account = await fetch_one("SELECT * FROM connected_accounts WHERE platform_id = ?", [platform])
        if not account or not account["refresh_token_enc"]:
            await log_system_error(
                error_code=ErrorCode.CREDENTIALS_REAUTH_REQUIRED,
                category=ErrorCategory.AUTH,
                message=f"No refresh token found for platform '{platform}'",
                platform_id=platform,
                status_code=409,
            )
            raise TokenServiceError(f"No refresh token found for platform '{platform}'", status_code=409)

        refresh_token = decrypt_token(account["refresh_token_enc"])
        provider = get_provider(platform)
        
        last_exception = None
        for attempt in range(1, max_retries + 1):
            try:
                new_tokens = await provider.refresh(refresh_token)
                now_ts = int(time.time())
                expires_at = now_ts + int(new_tokens.get("expires_in", 3600))
                refresh_expires_in = new_tokens.get("refresh_expires_in")
                refresh_expires_at = (now_ts + int(refresh_expires_in)) if refresh_expires_in else account["refresh_expires_at"]

                # Encrypt new tokens
                new_access_enc = encrypt_token(new_tokens["access_token"])
                # Fallback to existing refresh token if not returned
                new_refresh = new_tokens.get("refresh_token") or refresh_token
                new_refresh_enc = encrypt_token(new_refresh)

                await execute(
                    """
                    UPDATE connected_accounts SET
                        access_token_enc = ?,
                        refresh_token_enc = ?,
                        expires_at = ?,
                        refresh_expires_at = ?,
                        status = 'connected',
                        last_refresh_at = ?,
                        last_error = NULL,
                        updated_at = ?
                    WHERE platform_id = ?
                    """,
                    [new_access_enc, new_refresh_enc, expires_at, refresh_expires_at, now_ts, now_ts, platform]
                )
                logger.info(f"Successfully refreshed token for platform '{platform}' (attempt {attempt}).")
                return {
                    "access_token": new_tokens["access_token"],
                    "expires_at": expires_at,
                }
            except Exception as e:
                last_exception = e
                logger.warning(f"Refresh attempt {attempt}/{max_retries} failed for '{platform}': {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)

        # If all retries failed, mark as needs_reauth and log error
        error_msg = f"Refresh failed after {max_retries} attempts: {last_exception}"
        await log_system_error(
            category=ErrorCategory.OAUTH,
            error_code=ErrorCode.TOKEN_REFRESH_FAILED,
            message=error_msg,
            platform_id=platform,
            status_code=409,
            exc=last_exception,
            context={"attempts": max_retries},
        )
        await execute(
            """
            UPDATE connected_accounts SET
                status = 'needs_reauth',
                last_error = ?,
                updated_at = ?
            WHERE platform_id = ?
            """,
            [error_msg, int(time.time()), platform]
        )
        raise TokenServiceError(f"Failed to refresh token for '{platform}': needs reauthorization", status_code=409)


async def get_valid_credentials(platform: str) -> Dict[str, Any]:
    """Retrieve guaranteed valid access token for platform. Auto-refresh if expiring within 10 minutes."""
    account = await fetch_one(
        """
        SELECT ca.*, p.handle, p.account_id
        FROM connected_accounts ca
        JOIN platforms p ON p.id = ca.platform_id
        WHERE ca.platform_id = ?
        """,
        [platform]
    )

    if not account or account["status"] == "disconnected" or not account["access_token_enc"]:
        raise TokenServiceError(f"Platform '{platform}' is not connected.", status_code=409)

    if account["status"] == "needs_reauth":
        raise TokenServiceError(f"Platform '{platform}' requires re-authentication.", status_code=409)

    now_ts = int(time.time())
    expires_at = account["expires_at"] or 0

    # Auto refresh if token expires in less than 10 minutes (600s)
    if expires_at - now_ts < 600:
        logger.info(f"Token for '{platform}' is expiring in {expires_at - now_ts}s. Refreshing now...")
        refresh_result = await refresh_platform_token(platform)
        access_token = refresh_result["access_token"]
        expires_at = refresh_result["expires_at"]
    else:
        access_token = decrypt_token(account["access_token_enc"])

    return {
        "access_token": access_token,
        "account_id": account["external_account_id"] or account["account_id"],
        "account_handle": account["handle"] or account["display_name"],
        "expires_at": expires_at,
    }


async def disconnect_account(platform: str) -> None:
    """Disconnect account: revoke token on platform (best effort) and update status in database."""
    account = await fetch_one("SELECT * FROM connected_accounts WHERE platform_id = ?", [platform])
    if account and account["access_token_enc"]:
        try:
            token = decrypt_token(account["access_token_enc"])
            if token:
                provider = get_provider(platform)
                await provider.revoke(token)
        except Exception as e:
            logger.warning(f"Error during revoke for {platform}: {e}")

    now_ts = int(time.time())
    await execute(
        """
        UPDATE connected_accounts SET
            access_token_enc = NULL,
            refresh_token_enc = NULL,
            status = 'disconnected',
            updated_at = ?
        WHERE platform_id = ?
        """,
        [now_ts, platform]
    )
    logger.info(f"Platform '{platform}' disconnected.")
