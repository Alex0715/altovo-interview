import asyncpg
from pgvector.asyncpg import register_vector

from .config import get_settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def create_pool() -> asyncpg.Pool:
    """Create the connection pool.

    statement_cache_size=0 is REQUIRED against Neon's pooled endpoint: the
    pooler runs pgbouncer in transaction mode, which does not guarantee the
    same backend across statements, so asyncpg's prepared-statement cache
    breaks with "prepared statement does not exist". This works fine on a
    direct connection too, so we set it unconditionally rather than sniffing
    the host and being wrong in one environment.
    """
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=8,
        statement_cache_size=0,
        init=_init_connection,
        command_timeout=30,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    return _pool
