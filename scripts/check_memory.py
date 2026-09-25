"""Live memory diagnostic. Run from backend-ai/ with the venv active, Ollama and PostgreSQL up:

    python ..\\scripts\\check_memory.py --q "what is in my notes about the blueprint"
    python ..\\scripts\\check_memory.py --verse 2.47 --style "I have been debugging for hours"
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Devanagari on Windows consoles
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

import asyncpg  # noqa: E402

from config import settings  # noqa: E402
from memory import episodes, vectorstore  # noqa: E402
from memory.db import close_pool, init_pool  # noqa: E402
from memory.embedder import EmbedError, embed_query  # noqa: E402
from memory.refs import pick_collection  # noqa: E402

_VERSE = re.compile(r"^\s*(\d{1,2})\s*[.:]\s*(\d{1,3})\s*$")


def _label(hit: dict) -> str:
    md = hit.get("metadata") or {}
    if hit["collection"] == "scripture" and "chapter" in md and "verse" in md:
        return f"Gita {md['chapter']}.{md['verse']}"
    return f"{Path(hit['source_path']).name}#{hit['ordinal']}"


def _snip(text: str, n: int = 80) -> str:
    return " ".join(text.split())[:n]


async def run(args: argparse.Namespace) -> int:
    pool = await init_pool(settings.pg_dsn)
    try:
        print("collection   documents  chunks  avg tokens")
        for r in await vectorstore.stats(pool):
            print(f"{r['collection']:<12} {r['documents']:>9}  {r['chunks']:>6}  {r['avg_tokens']:>10}")

        for q in args.q:
            col = pick_collection(q)
            print(f"\nQ: {q}\n   collection: {col}")
            hits = await vectorstore.search(pool, await embed_query(q), q, [col], top=8)
            if not hits:
                print("   (no hits)")
            for i, h in enumerate(hits, 1):
                vec = "-" if h["vec_score"] is None else f"{h['vec_score']:.3f}"
                print(f"   {i}. rrf {h['score']:.4f}  vec {vec}  lex {h['lex_score']:.3f}  "
                      f"{_label(h)}  {_snip(h['content'])}")
            print("   note: the vec score (cosine similarity) is the number to use for any "
                  "relevance floor; the fused rrf score only ranks.")

        if args.verse:
            m = _VERSE.match(args.verse)
            if not m:
                print(f"\n--verse expects chapter.verse, e.g. 2.47 (got {args.verse!r})")
                return 1
            row = await vectorstore.get_verse(pool, int(m[1]), int(m[2]))
            print(f"\nVerse {m[1]}.{m[2]}:")
            print(row["content"] if row else "not found")

        if args.style:
            print(f"\nStyle examples nearest to: {args.style}")
            for ex in await vectorstore.nearest_dialogues(pool, await embed_query(args.style),
                                                          settings.style_examples or 3):
                print("---\n" + ex)

        print("\nLast 10 episodes:")
        rows = await episodes.recent(pool, 10)
        if not rows:
            print("(none)")
        for e in rows:
            print(f"{e['created_at']:%Y-%m-%d %H:%M:%S}  {e['agent']:<10} {e['outcome']:<9} "
                  f"{e['run_id'][:8]}  {_snip(e['summary'])}")
    finally:
        await close_pool(pool)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect the memory store.")
    ap.add_argument("--q", action="append", default=[], help="question to search (repeatable)")
    ap.add_argument("--verse", help='verse lookup, e.g. "2.47"')
    ap.add_argument("--style", help="text to find the nearest style examples for")
    try:
        return asyncio.run(run(ap.parse_args()))
    except EmbedError as exc:
        print(f"embedding error: {exc}")
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        print(f"database error: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
