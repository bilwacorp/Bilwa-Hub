"""Test harness for HUB-Expansion.md Phase 20. Uses a real, dedicated
Postgres database (`bilwacorp_hub_test`) rather than SQLite — this
codebase uses native Postgres enum types and JSON/JSONB columns
extensively (see docs/architecture/current-state.md), which SQLite can't
faithfully reproduce, so a SQLite-backed test suite would pass against a
database the app never actually runs on.

Per-test isolation: one Postgres connection per test, wrapped in an outer
transaction that's rolled back at the end (`db_session` fixture) — every
session created during the test (including the ones FastAPI's overridden
`get_db` dependency creates per request) is bound to that same connection
via `join_transaction_mode="create_savepoint"`, so a request's own
`session.commit()` just releases a savepoint instead of really committing;
nothing written during a test outlives it.

The Casbin enforcer is initialized once per test session (after migrations
seed the seven built-in permission-catalog migrations' policy rows) and
kept in memory for the whole run — `enforce()` is pure in-memory, so it
doesn't care about per-test transaction rollback. New test users are
assigned a role via services/rbac.set_role, which updates the same
in-memory enforcer directly.
"""
import os
import subprocess
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB_NAME = "bilwacorp_hub_test"
_ADMIN_URL = "postgresql://gopalsmac@localhost:5432/postgres"
_TEST_DB_URL = f"postgresql+asyncpg://gopalsmac@localhost:5432/{TEST_DB_NAME}"

# Must happen before any `app.*` import (below) — Settings() (app/core/
# config.py) reads DATABASE_URL once at import time, and app/db/session.py
# builds its engine from that value immediately.
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ.setdefault("DB_SSL_MODE", "disable")
# Required Settings fields with no default (app/core/config.py) — no test
# ever drives a live Authentik round-trip (see as_user() below), so these
# just need to be non-empty strings for Settings() to construct.
os.environ.setdefault("AUTHENTIK_ISSUER", "http://authentik.invalid/application/o/test/")
os.environ.setdefault("AUTHENTIK_CLIENT_ID", "test-client-id")
os.environ.setdefault("AUTHENTIK_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AUTHENTIK_REDIRECT_URI", "http://test/api/v1/auth/callback")


def _recreate_test_database() -> None:
    subprocess.run(
        ["psql", _ADMIN_URL, "-v", "ON_ERROR_STOP=1", "-c", f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["psql", _ADMIN_URL, "-v", "ON_ERROR_STOP=1", "-c", f"CREATE DATABASE {TEST_DB_NAME}"],
        check=True, capture_output=True, text=True,
    )


def _run_migrations() -> None:
    env = {**os.environ, "DATABASE_URL": _TEST_DB_URL}
    subprocess.run(["alembic", "upgrade", "head"], cwd=BACKEND_DIR, env=env, check=True, capture_output=True, text=True)


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    _recreate_test_database()
    _run_migrations()
    yield


# Imported only after DATABASE_URL is pointed at the test database (see
# above) and only inside a fixture body / at module scope after that
# assignment — never move these above the os.environ line.
from app.core import casbin_enforcer  # noqa: E402
from app.core.deps import get_current_user  # noqa: E402
from app.db.session import engine as app_engine  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import User  # noqa: E402
from app.services import rbac  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def _enforcer(_test_database):
    await casbin_enforcer.init_enforcer()


@pytest_asyncio.fixture
async def db_connection(_test_database):
    async with app_engine.connect() as conn:
        yield conn


@pytest_asyncio.fixture
async def db_session(db_connection, _enforcer):
    """A session bound to `db_connection`'s outer transaction, handed to
    the test for setup/assertions. FastAPI's `get_db` is overridden for
    the duration to hand out sessions on the SAME connection (so a
    request's writes are visible to the test and vice versa), then the
    outer transaction is rolled back — discarding everything, including
    anything a request "committed" through the override."""
    trans = await db_connection.begin()
    TestSessionLocal = async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
    )

    async def _override_get_db():
        session = TestSessionLocal()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    session = TestSessionLocal()
    try:
        yield session
    finally:
        await session.close()
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(get_current_user, None)
        await trans.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    """Plain (unauthenticated) client — most tests use `as_user()` below
    to also stand in for a specific staff user."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


def as_user(user: User) -> None:
    """Makes every subsequent request on the current test's `client` see
    `user` as the authenticated caller — bypasses the Authentik OIDC round
    trip entirely (a live IdP isn't available in this test harness; see
    docs/adr/ADR-012-authentik-sso.md)."""
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


async def make_user(db_session, *, role: str | None = None, username: str | None = None) -> User:
    username = username or f"test_{uuid.uuid4().hex[:10]}"
    user = User(username=username, email=f"{username}@bilwacorp.example", is_active=True)
    db_session.add(user)
    await db_session.flush()
    if role:
        await rbac.set_role(str(user.id), role)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session) -> User:
    return await make_user(db_session, role="admin")


@pytest_asyncio.fixture
async def engineer_user(db_session) -> User:
    return await make_user(db_session, role="engineer")


@pytest_asyncio.fixture
async def no_role_user(db_session) -> User:
    """A real, active staff account with no Casbin role at all — every
    require_permission(...) dependency should reject it."""
    return await make_user(db_session, role=None)
