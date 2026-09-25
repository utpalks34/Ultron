"""Live check of the PostgreSQL LangGraph checkpointer. Run from backend-ai/ with the venv active
and PostgreSQL up:

    python ..\\scripts\\check_checkpointer.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

import asyncpg  # noqa: E402

from config import settings  # noqa: E402
from graph.checkpointer import make_checkpointer  # noqa: E402


async def run() -> None:
    saver, closer = await make_checkpointer("postgres", settings.pg_dsn)
    try:
        print("checkpointer OK")
        conn = await asyncpg.connect(settings.pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'checkpoint%' ORDER BY table_name")
        finally:
            await conn.close()
        for r in rows:
            print(f"  {r['table_name']}")
        if not rows:
            print("  (no checkpoint tables found)")
    finally:
        if closer:
            await closer()


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"checkpointer FAILED: {exc.__class__.__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
