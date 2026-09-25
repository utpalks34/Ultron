"""LangGraph checkpointer factory. psycopg async cannot run on Windows' default Proactor
event loop, so the backend runs a Selector loop (see orchestrator __main__). AsyncPostgresSaver
needs a psycopg pool with autocommit=True, prepare_threshold=0 and dict_row, NOT the asyncpg
pool used elsewhere."""


async def make_checkpointer(kind: str, dsn: str):
    """Returns (saver, closer). closer is an async callable or None."""
    if kind == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            dsn, min_size=1, max_size=3, open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        await pool.open(wait=True, timeout=10)
        try:
            saver = AsyncPostgresSaver(pool)
            await saver.setup()          # idempotent: creates the checkpoint tables
        except BaseException:
            await pool.close()
            raise
        return saver, pool.close
    try:
        from langgraph.checkpoint.memory import InMemorySaver as _Saver
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver as _Saver
    return _Saver(), None
