import os
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
import httpx
from httpx import ASGITransport

# Set up test environment variables BEFORE importing app modules
_test_fernet_key = Fernet.generate_key().decode("utf-8")
_temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_temp_db_path = _temp_db_file.name
_temp_db_file.close()

os.environ["TOKEN_ENCRYPTION_KEY"] = _test_fernet_key
os.environ["APP_SECRET_KEY"] = "test_app_secret_key_at_least_32_chars_12345"
os.environ["INGEST_API_KEY"] = "test_ingest_key_valid_123"
os.environ["ADMIN_USERNAME"] = "admin"
# bcrypt hash for "admin123"
os.environ["ADMIN_PASSWORD_HASH"] = "$2b$12$123456789012345678901e1F0BfqM3uJ17z9qj2.FkGgTf7kHjAmi"  # We will test auth with real hash
os.environ["TURSO_DATABASE_URL"] = f"file:{_temp_db_path}"
os.environ["TURSO_AUTH_TOKEN"] = ""
os.environ["BASE_URL"] = "http://testserver"

from app.config import get_settings
# Invalidate cache so settings read new env
get_settings.cache_clear()

from app.db import close_client, execute, get_client
from app.main import app
from migrations.runner import run_migrations


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_test_db():
    # Run initial migrations
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    await run_migrations(migrations_dir=migrations_dir)
    yield
    await close_client()
    try:
        os.remove(_temp_db_path)
    except Exception:
        pass


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
