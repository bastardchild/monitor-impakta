from datetime import datetime, timezone, timedelta
import json
import logging
import re
import traceback
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo
from app.config import get_settings
from app.db import execute, fetch_all

logger = logging.getLogger("kpi-sosmed.errors")


# Standardized Error Categories
class ErrorCategory:
    OAUTH = "OAUTH"
    INGEST = "INGEST"
    AUTH = "AUTH"
    DATABASE = "DATABASE"
    BACKGROUND = "BACKGROUND"
    SYSTEM = "SYSTEM"


# Standardized Error Codes
class ErrorCode:
    # OAuth errors
    OAUTH_TOKEN_EXCHANGE_FAILED = "OAUTH_TOKEN_EXCHANGE_FAILED"
    OAUTH_STATE_INVALID = "OAUTH_STATE_INVALID"
    OAUTH_SCOPE_INSUFFICIENT = "OAUTH_SCOPE_INSUFFICIENT"
    OAUTH_WEBHOOK_FAILED = "OAUTH_WEBHOOK_FAILED"

    # Ingestion errors
    INGEST_PAYLOAD_INVALID = "INGEST_PAYLOAD_INVALID"
    INGEST_DB_ERROR = "INGEST_DB_ERROR"
    INGEST_AUTH_FAILED = "INGEST_AUTH_FAILED"

    # Credentials & Tokens
    CREDENTIALS_NOT_FOUND = "CREDENTIALS_NOT_FOUND"
    CREDENTIALS_REAUTH_REQUIRED = "CREDENTIALS_REAUTH_REQUIRED"
    TOKEN_REFRESH_FAILED = "TOKEN_REFRESH_FAILED"

    # Database
    DATABASE_QUERY_ERROR = "DATABASE_QUERY_ERROR"
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"

    # General HTTP / System
    VALIDATION_ERROR = "VALIDATION_ERROR"
    HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


SENSITIVE_KEYS_PATTERN = re.compile(
    r"(token|secret|password|key|authorization|bearer|cookie|access_token|refresh_token|api_key)",
    re.IGNORECASE
)


def sanitize_data(data: Any) -> Any:
    """Recursively mask sensitive values in dicts, lists, and strings."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if SENSITIVE_KEYS_PATTERN.search(str(k)):
                sanitized[k] = "[MASKED_SECRET]"
            else:
                sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        # Mask Bearer tokens if in string
        data = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.\~]+", r"\1[MASKED_TOKEN]", data, flags=re.IGNORECASE)
        # Mask potential client secrets
        data = re.sub(r"(client_secret=[^&\s]+)", r"client_secret=[MASKED]", data, flags=re.IGNORECASE)
        return data
    return data


async def log_system_error(
    category: str,
    error_code: str,
    message: str,
    exc: Optional[BaseException] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
    platform_id: Optional[str] = None,
    status_code: int = 500,
    context: Optional[Dict[str, Any]] = None,
    error_id: Optional[str] = None,
    persist: bool = True,
) -> str:
    """
    Standardized Error Logger:
    1. Generates unique error_id.
    2. Masks sensitive credentials and tokens.
    3. Emits structured JSON log to stdout for Docker/monitoring.
    4. Persists error event to SQLite/libSQL system_error_logs table.
    """
    err_id = error_id or f"err_{uuid.uuid4().hex[:12]}"
    now_utc = datetime.now(timezone.utc).isoformat()
    exc_type = type(exc).__name__ if exc else None

    # Sanitize context and stack trace
    sanitized_ctx = sanitize_data(context) if context else {}
    if exc:
        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        sanitized_ctx["traceback"] = sanitize_data(tb_str)

    details_json_str = json.dumps(sanitized_ctx)

    # 1. Output structured JSON to logger
    log_payload = {
        "timestamp": now_utc,
        "level": "ERROR",
        "error_id": err_id,
        "error_code": error_code,
        "category": category,
        "message": message,
        "path": path,
        "method": method,
        "platform_id": platform_id,
        "status_code": status_code,
        "exception_type": exc_type,
        "context": sanitized_ctx,
    }
    logger.error(json.dumps(log_payload))

    # 2. Persist to database if requested
    if persist:
        try:
            await execute(
                """
                INSERT INTO system_error_logs (
                    id, error_code, category, message, path, method,
                    platform_id, status_code, exception_type, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    err_id, error_code, category, message, path, method,
                    platform_id, status_code, exc_type, details_json_str, now_utc
                ]
            )
        except Exception as db_err:
            # Fallback: do not crash caller if database logging fails
            logger.warning(f"Failed to persist system error log {err_id} to DB: {db_err}")

    return err_id


async def get_recent_error_logs(
    limit: int = 50,
    category: Optional[str] = None,
    platform_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve recent system error logs formatted for the dashboard UI."""
    settings = get_settings()
    tz = ZoneInfo(settings.TZ)

    conditions = []
    args: List[Any] = []

    if category and category != "all":
        conditions.append("category = ?")
        args.append(category)

    if platform_id and platform_id != "all":
        conditions.append("platform_id = ?")
        args.append(platform_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT id, error_code, category, message, path, method, platform_id,
               status_code, exception_type, details_json, created_at
        FROM system_error_logs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ?
    """
    rows = await fetch_all(sql, args + [limit])

    for r in rows:
        try:
            dt = datetime.fromisoformat(r["created_at"]).astimezone(tz)
            r["created_at_formatted"] = dt.strftime("%d/%m/%Y, %H:%M:%S WIB")
        except Exception:
            r["created_at_formatted"] = r["created_at"]

        # Parse details JSON for modal / drawer
        try:
            r["details"] = json.loads(r["details_json"]) if r["details_json"] else {}
        except Exception:
            r["details"] = {}

        # Category Badge styling
        cat = r["category"]
        if cat == ErrorCategory.OAUTH:
            r["badge_class"] = "bg-amber-500/10 text-amber-400 border-amber-500/20"
        elif cat == ErrorCategory.INGEST:
            r["badge_class"] = "bg-blue-500/10 text-blue-400 border-blue-500/20"
        elif cat == ErrorCategory.AUTH:
            r["badge_class"] = "bg-purple-500/10 text-purple-400 border-purple-500/20"
        elif cat == ErrorCategory.DATABASE:
            r["badge_class"] = "bg-rose-500/10 text-rose-400 border-rose-500/20"
        elif cat == ErrorCategory.BACKGROUND:
            r["badge_class"] = "bg-teal-500/10 text-teal-400 border-teal-500/20"
        else:
            r["badge_class"] = "bg-slate-800 text-slate-300 border-slate-700"

    return rows


async def prune_old_error_logs(retention_days: int = 30) -> int:
    """Delete logs older than retention_days to prevent database bloat."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    res = await execute(
        "DELETE FROM system_error_logs WHERE created_at < ?",
        [cutoff]
    )
    # ResultSet rows affected
    return getattr(res, "rows_affected", 0)
