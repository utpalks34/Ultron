"""asyncpg pool. Vectors are passed as $n::text::vector (no vector codec needed);
jsonb gets a codec so chunk metadata round-trips as a dict."""
import json
import logging

import asyncpg

log = logging.getLogger("ultron.db")


async def _init(conn) -> None:            # once per connection (client-side codec)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")


async def _setup(conn) -> None:           # on EVERY acquire: asyncpg resets session state on release
    for stmt in ("SET hnsw.ef_search = 80", "SET hnsw.iterative_scan = 'relaxed_order'"):
        try:
            await conn.execute(stmt)
        except Exception as exc:
            log.warning("%s failed: %s", stmt, exc)


async def init_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, timeout=10,
                                     command_timeout=60, init=_init, setup=_setup)


async def close_pool(pool) -> None:
    if pool is not None:
        await pool.close()
