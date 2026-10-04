import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from app.db import get_client, close_client

logger = logging.getLogger(__name__)


def parse_sql_statements(content: str) -> list[str]:
    """Parse SQL statements from file, ignoring comments and blank lines."""
    statements = []
    current = []
    
    for line in content.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("--"):
            continue
        current.append(line)
        if trimmed.endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            
    if current:
        remaining = "\n".join(current).strip()
        if remaining:
            statements.append(remaining)
            
    return statements


async def run_migrations(migrations_dir: Path | None = None) -> None:
    if migrations_dir is None:
        migrations_dir = Path(__file__).resolve().parent

    client = get_client()
    
    # 1. Ensure migrations table exists
    await client.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    
    # 2. Get list of already applied migrations
    applied_res = await client.execute("SELECT version FROM schema_migrations")
    applied_versions = {row[0] for row in applied_res.rows}
    
    # 3. Find and sort migration files
    migration_files = sorted(migrations_dir.glob("*.sql"))
    
    for file_path in migration_files:
        version = file_path.name
        if version in applied_versions:
            logger.debug(f"Migration already applied: {version}")
            continue
            
        logger.info(f"Applying migration: {version}")
        content = file_path.read_text(encoding="utf-8")
        statements = parse_sql_statements(content)
        
        for stmt in statements:
            await client.execute(stmt)
            
        applied_at = datetime.now(timezone.utc).isoformat()
        await client.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [version, applied_at]
        )
        logger.info(f"Migration {version} applied successfully.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migrations())
