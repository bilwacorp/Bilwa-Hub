from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.core.config import settings

_db_url = settings.DATABASE_URL
for _old in ("postgresql://", "postgresql+psycopg2://", "postgres://"):
    if _db_url.startswith(_old):
        _db_url = "postgresql+asyncpg://" + _db_url[len(_old):]
        break

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args={"ssl": settings.DB_SSL_MODE, "statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def make_celery_sessionmaker() -> async_sessionmaker:
    """A dedicated NullPool engine/sessionmaker for a Celery tasks module
    — first extracted here from services/notifications/tasks.py so
    app/integrations/github/tasks.py doesn't have to duplicate it.
    NullPool (not the pooled `engine` above) because a Celery task calls
    asyncio.run() per invocation (see _run_async in either tasks module),
    so every task gets a fresh event loop — a pooled asyncpg connection is
    bound to the loop that created it, so reusing one across tasks/loops
    breaks with "attached to a different loop". Every domain's Celery
    tasks module should call this once at its own import time for its own
    sessionmaker/engine — NullPool means there's no connection pooling to
    share across domains anyway, so a shared instance would only add
    import-order coupling for no benefit."""
    engine_ = create_async_engine(
        _db_url, echo=False, poolclass=NullPool,
        connect_args={"ssl": settings.DB_SSL_MODE, "statement_cache_size": 0},
    )
    return async_sessionmaker(engine_, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False)
