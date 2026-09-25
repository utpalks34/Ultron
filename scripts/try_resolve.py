"""Shows how a phrase resolves against the real registry tables. Run from backend-ai/ with the
venv active and PostgreSQL up:

    python ..\\scripts\\try_resolve.py app "editor"
    python ..\\scripts\\try_resolve.py media "something calm"
    python ..\\scripts\\try_resolve.py contact "arjun"
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

import asyncpg  # noqa: E402

from config import settings  # noqa: E402
from memory.db import close_pool, init_pool  # noqa: E402
from tools.resolve import CONFIDENT, FLOOR, MARGIN, normalize, resolve  # noqa: E402

SHOW = {
    "app": ("name", "aliases", "launch_cmd", "process_name", "protected"),
    "media": ("title", "artist", "path", "tags"),
    "contact": ("name", "aliases", "phone", "channel"),
}


async def run(args: argparse.Namespace) -> int:
    pool = await init_pool(settings.pg_dsn)
    try:
        decision, rows = await resolve(pool, args.kind, args.phrase)
    finally:
        await close_pool(pool)

    print(f"phrase: {args.phrase!r}  normalized: {normalize(args.phrase)!r}")
    print(f"thresholds: confident >= {CONFIDENT}, margin > {MARGIN}, floor {FLOOR}")
    print(f"decision: {decision}")
    if not rows:
        print("  (no candidates)")
    for i, r in enumerate(rows, 1):
        print(f"  {i}. score {r['score']:.1f}")
        for f in SHOW[args.kind]:
            print(f"       {f}: {r.get(f)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve a phrase against the registry.")
    ap.add_argument("kind", choices=["app", "media", "contact"])
    ap.add_argument("phrase")
    try:
        return asyncio.run(run(ap.parse_args()))
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        print(f"database error: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
