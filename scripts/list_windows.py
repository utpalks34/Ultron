"""Shows what "close everything" WOULD close, and what it would protect and why.
It never closes anything. Run from backend-ai/ with the venv active:

    python ..\\scripts\\list_windows.py

The registry part needs PostgreSQL; without it the script says so and skips that part.
"""
import asyncio
import sys
from pathlib import Path

import psutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

from config import settings  # noqa: E402
from memory.db import close_pool, init_pool  # noqa: E402
from tools.desktop import PROTECTED_NAMES, ancestor_pids, filter_targets, visible_windows  # noqa: E402


def ancestor_names(ancestors: set[int]) -> set[str]:
    """Lower-cased image names of the backend's ancestors (taskkill works by image name)."""
    names = set()
    for pid in ancestors:
        try:
            names.add(psutil.Process(pid).name().lower())
        except psutil.Error:
            pass
    return names


async def registry_protected() -> set[str] | None:
    """Lower-cased process names of registry_apps rows with protected = true, or None."""
    try:
        pool = await init_pool(settings.pg_dsn)
    except Exception as exc:
        print(f"(registry check skipped: database not reachable: {exc})")
        return None
    try:
        rows = await pool.fetch(
            "SELECT process_name FROM registry_apps WHERE protected AND process_name IS NOT NULL")
        return {r["process_name"].lower() for r in rows}
    except Exception as exc:
        print(f"(registry check skipped: {exc})")
        return None
    finally:
        await close_pool(pool)


def main() -> int:
    windows = visible_windows()
    ancestors = ancestor_pids()
    registry = asyncio.run(registry_protected())
    ancestor_imgs = ancestor_names(ancestors)
    names = {n.lower() for n in PROTECTED_NAMES} | (registry or set()) | ancestor_imgs

    targets = filter_targets(windows, names, ancestors)
    print(f"\nWould close ({len(targets)} processes):")
    if not targets:
        print("  (nothing)")
    for t in targets:
        print(f"  {t['name']}  (pid {t['pid']})")
        for title in t["titles"]:
            print(f"      {title[:100]}")

    print("\nProtected (never closed):")
    kept = 0
    for w in sorted(windows, key=lambda w: (w["name"].lower(), w["title"])):
        reasons = []
        if w["name"].lower() in {n.lower() for n in PROTECTED_NAMES}:
            reasons.append("protected name")
        if w["pid"] in ancestors:
            reasons.append("backend or its ancestor")
        if w["name"].lower() in ancestor_imgs:
            reasons.append("image name of the backend's ancestor")
        if registry and w["name"].lower() in registry:
            reasons.append("registry protected row")
        if not reasons:
            continue
        kept += 1
        print(f"  {w['name']}  (pid {w['pid']})  [{', '.join(reasons)}]  {w['title'][:80]}")
    if not kept:
        print("  (none)")

    print(f"\n{len(windows)} visible windows seen. Nothing was closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
