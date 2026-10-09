import logging
from typing import Any, Dict, List, Optional
import aiohttp
import libsql_client
from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[libsql_client.Client] = None


def get_client() -> libsql_client.Client:
    global _client
    if _client is None:
        settings = get_settings()
        url = settings.TURSO_DATABASE_URL
        auth_token = settings.TURSO_AUTH_TOKEN if settings.TURSO_AUTH_TOKEN else None
        
        # When using file: scheme, auth_token is not needed
        if url.startswith("file:") or url.startswith("sqlite:"):
            _client = libsql_client.create_client(url)
        else:
            if url.startswith("libsql://"):
                url = url.replace("libsql://", "https://", 1)
            _client = libsql_client.create_client(url, auth_token=auth_token)
            
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None


async def execute(sql: str, args: Optional[List[Any]] = None) -> libsql_client.ResultSet:
    for attempt in range(3):
        client = get_client()
        try:
            return await client.execute(sql, args or [])
        except (aiohttp.ClientError, ConnectionError, Exception) as e:
            # If server disconnected or connection reset on idle keep-alive socket, reconnect and retry
            err_str = str(e).lower()
            is_conn_error = isinstance(e, (aiohttp.ClientError, ConnectionError)) or "disconnect" in err_str or "connection" in err_str
            if attempt < 2 and is_conn_error:
                logger.warning(f"Database connection error (attempt {attempt + 1}/3): {e}. Reconnecting...")
                await close_client()
                continue
            raise


async def batch(statements: List[Any]) -> List[libsql_client.ResultSet]:
    for attempt in range(3):
        client = get_client()
        try:
            return await client.batch(statements)
        except (aiohttp.ClientError, ConnectionError, Exception) as e:
            err_str = str(e).lower()
            is_conn_error = isinstance(e, (aiohttp.ClientError, ConnectionError)) or "disconnect" in err_str or "connection" in err_str
            if attempt < 2 and is_conn_error:
                logger.warning(f"Database batch connection error (attempt {attempt + 1}/3): {e}. Reconnecting...")
                await close_client()
                continue
            raise


async def fetch_all(sql: str, args: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    res = await execute(sql, args)
    cols = res.columns
    return [{col: row[col] for col in cols} for row in res.rows]


async def fetch_one(sql: str, args: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    res = await execute(sql, args)
    if not res.rows:
        return None
    cols = res.columns
    return {col: res.rows[0][col] for col in cols}
