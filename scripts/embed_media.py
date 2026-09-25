"""Embed registry_media rows that have no embedding yet ("title - artist - tags"), so mood requests
like "play something calm" can match by meaning. Safe to re-run: only NULL rows are touched.
Run from backend-ai/ with the venv active, Ollama and PostgreSQL up:

    python ..\\scripts\\embed_media.py
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

import asyncpg  # noqa: E402

from config import settings  # noqa: E402
from memory.db import close_pool, init_pool  # noqa: E402
from memory.embedder import EmbedError, embed_documents  # noqa: E402
from memory.vectorstore import vec_literal  # noqa: E402


def _text(row) -> str:
    parts = [row["title"], row["artist"], " ".join(row["tags"] or [])]
    return " - ".join(p for p in parts if p)


async def run() -> int:
    pool = await init_pool(settings.pg_dsn)
    try:
        rows = await pool.fetch(
            "SELECT id, title, artist, tags FROM registry_media WHERE embedding IS NULL ORDER BY id")
        print(f"tracks without an embedding: {len(rows)}")
        done = 0
        for i in range(0, len(rows), settings.embed_batch):
            batch = rows[i:i + settings.embed_batch]
            vecs = await embed_documents([_text(r) for r in batch])
            await pool.executemany(
                "UPDATE registry_media SET embedding = $1::text::vector WHERE id = $2",
                [(vec_literal(v), r["id"]) for v, r in zip(vecs, batch)])
            done += len(batch)
            print(f"  embedded {done}/{len(rows)}")
        remaining = await pool.fetchval(
            "SELECT count(*) FROM registry_media WHERE embedding IS NULL")
        total = await pool.fetchval("SELECT count(*) FROM registry_media")
        print(f"embedded now: {done}  still without: {remaining}  total tracks: {total}")
    finally:
        await close_pool(pool)
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except EmbedError as exc:
        print(f"embedding error: {exc}")
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        print(f"database error: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
