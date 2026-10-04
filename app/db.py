import logging
from typing import Any, Dict, List, Optional
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
            _client = libsql_client.create_client(url, auth_token=auth_token)
            
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def execute(sql: str, args: Optional[List[Any]] = None) -> libsql_client.ResultSet:
    client = get_client()
    return await client.execute(sql, args or [])


async def batch(statements: List[Any]) -> List[libsql_client.ResultSet]:
    client = get_client()
    return await client.batch(statements)


async def fetch_all(sql: str, args: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    client = get_client()
    res = await client.execute(sql, args or [])
    cols = res.columns
    return [{col: row[col] for col in cols} for row in res.rows]


async def fetch_one(sql: str, args: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    client = get_client()
    res = await client.execute(sql, args or [])
    if not res.rows:
        return None
    cols = res.columns
    return {col: res.rows[0][col] for col in cols}
