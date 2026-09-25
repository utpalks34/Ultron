"""Ingest CLI. Run from backend-ai/ (config reads .env from the cwd):

    python -m memory.ingest --collection personal --path ..\\data\\notes
    python -m memory.ingest --collection scripture --path ..\\data\\scripture --dry-run

Only the local Ollama server is contacted (embeddings); nothing else leaves the machine."""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import asyncpg

from config import settings
from memory import vectorstore
from memory.chunker import chunk_text, est_tokens
from memory.db import close_pool, init_pool
from memory.embedder import EmbedError, embed_documents

ACCEPT = {"personal": {".md", ".txt"}, "scripture": {".jsonl", ".json"},
          "dialogue": {".md", ".txt"}}
MIME = {".md": "text/markdown", ".txt": "text/plain", ".jsonl": "application/jsonl",
        ".json": "application/json"}
_H1 = re.compile(r"(?m)^#\s+(.+?)\s*$")
_SEP = re.compile(r"(?m)^\s*---\s*$")
DIALOGUE_MAX_TOKENS = 600

Unit = tuple[str, dict]                     # (content, metadata)


def _collect(path: Path, collection: str) -> list[Path]:
    ok = ACCEPT[collection]
    if path.is_file():
        return [path] if path.suffix.lower() in ok else []
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in ok)
    return []


def _parse_personal(text: str, stem: str) -> tuple[str, list[Unit], list[str], int]:
    m = _H1.search(text)
    title = m.group(1) if m else stem
    return title, [(c, {}) for c in chunk_text(text)], [], 0


def _verse_problem(obj) -> str | None:
    if not isinstance(obj, dict):
        return "not a JSON object"
    ch, vs = obj.get("chapter"), obj.get("verse")
    if type(ch) is not int or not 1 <= ch <= 18:
        return "chapter must be an integer 1-18"
    if type(vs) is not int or vs < 1:
        return "verse must be an integer >= 1"
    tr = obj.get("translation")
    if not isinstance(tr, str) or not tr.strip():
        return "translation must be a non-empty string"
    for key in ("sanskrit", "transliteration"):
        if obj.get(key) is not None and not isinstance(obj[key], str):
            return f"{key} must be a string"
    return None


def _parse_scripture(text: str, suffix: str, label: str) -> tuple[str, list[Unit], list[str], int]:
    msgs: list[str] = []
    bad = 0
    records: list[tuple[str, object]] = []
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return label, [], [f"{label}: invalid JSON ({exc.msg})"], 1
        if not isinstance(data, list):
            return label, [], [f"{label}: expected a JSON array"], 1
        records = [(f"{label}:item {i}", o) for i, o in enumerate(data, 1)]
    else:
        for n, line in enumerate(text.split("\n"), 1):
            if not line.strip():
                continue
            try:
                records.append((f"{label}:{n}", json.loads(line)))
            except json.JSONDecodeError as exc:
                msgs.append(f"{label}:{n}: invalid JSON ({exc.msg})")
                bad += 1
    units: list[Unit] = []
    seen: set[tuple[int, int]] = set()
    for where, obj in records:
        reason = _verse_problem(obj)
        if reason:
            msgs.append(f"{where}: {reason}")
            bad += 1
            continue
        c, v = obj["chapter"], obj["verse"]
        if (c, v) in seen:
            msgs.append(f"{where}: warning: duplicate {c}.{v} skipped")
            continue
        seen.add((c, v))
        lines = [f"Bhagavad Gita {c}.{v}"]
        for key in ("sanskrit", "transliteration", "translation"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                lines.append(val.strip())
        units.append(("\n".join(lines), {"chapter": c, "verse": v}))
    return Path(label).stem, units, msgs, bad


def _parse_dialogue(text: str, label: str) -> tuple[str, list[Unit], list[str], int]:
    msgs: list[str] = []
    units: list[Unit] = []
    for i, block in enumerate((b.strip() for b in _SEP.split(text)), 1):
        if not block:
            continue
        if "User:" not in block or "Ultron:" not in block:
            msgs.append(f"{label}: warning: block {i} lacks 'User:' or 'Ultron:'")
        if est_tokens(block) > DIALOGUE_MAX_TOKENS:
            msgs.append(f"{label}: warning: block {i} exceeds {DIALOGUE_MAX_TOKENS} tokens")
        units.append((block, {}))
    return Path(label).stem, units, msgs, 0


def _parse(collection: str, path: Path, text: str) -> tuple[str, list[Unit], list[str], int]:
    if collection == "personal":
        return _parse_personal(text, path.stem)
    if collection == "scripture":
        return _parse_scripture(text, path.suffix.lower(), str(path))
    return _parse_dialogue(text, str(path))


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m memory.ingest", description=__doc__.split("\n")[0])
    ap.add_argument("--collection", required=True, choices=sorted(ACCEPT))
    ap.add_argument("--path", required=True, help="file or folder (folders are walked recursively)")
    ap.add_argument("--dry-run", action="store_true", help="parse and chunk only")
    ap.add_argument("--reset", action="store_true",
                    help="delete every document of the collection first (needs --yes)")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    return ap.parse_args()


async def main() -> int:
    args = _args()
    if args.reset and not args.yes:
        print("--reset deletes every document in the collection; add --yes to confirm.")
        return 1
    files = _collect(Path(args.path), args.collection)
    if not files:
        print(f"no {'/'.join(sorted(ACCEPT[args.collection]))} files found at {args.path}")
        return 1

    t0 = time.monotonic()
    written = skipped = failed = 0
    pool = None
    try:
        if not args.dry_run:
            try:
                pool = await init_pool(settings.pg_dsn)
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
                print(f"database unavailable: {exc}")
                return 1
            if args.reset:
                await pool.execute("DELETE FROM documents WHERE collection = $1", args.collection)
                print(f"reset: removed every {args.collection} document")

        for f in files:
            try:
                raw = f.read_bytes()
                text = raw.decode("utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                print(f"{f}: cannot read ({exc})")
                failed += 1
                continue
            sha = hashlib.sha256(raw).hexdigest()
            title, units, msgs, bad = _parse(args.collection, f, text)
            for m in msgs:
                print(f"  {m}")
            failed += bad

            if args.dry_run:
                if units:
                    toks = [est_tokens(c) for c, _ in units]
                    print(f"{f}: {len(units)} chunks, avg {sum(toks) / len(toks):.0f}, "
                          f"max {max(toks)} tokens")
                    written += len(units)
                else:
                    print(f"{f}: no chunks")
                continue

            if not units:
                print(f"{f}: no chunks, skipped")
                skipped += 1
                continue
            key = str(f.resolve())
            try:
                if await vectorstore.document_state(pool, key) == (sha, args.collection):
                    print(f"{f}: unchanged, skipped")
                    skipped += 1
                    continue
                vecs = await embed_documents([c for c, _ in units])
                await vectorstore.replace_document(
                    pool, source_path=key, title=title,
                    mime=MIME.get(f.suffix.lower(), "text/plain"), sha256=sha,
                    collection=args.collection,
                    chunks=[{"content": c, "token_count": est_tokens(c), "metadata": md,
                             "embedding": vec} for (c, md), vec in zip(units, vecs)])
            except EmbedError as exc:
                print(f"{f}: embedding failed: {exc}")
                failed += 1
                continue
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
                print(f"{f}: database error: {exc}")
                failed += 1
                continue
            print(f"{f}: {len(units)} chunks written")
            written += len(units)

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    for stmt in ("VACUUM ANALYZE chunks", "VACUUM ANALYZE documents"):
                        await conn.execute(stmt)
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
                print(f"warning: VACUUM ANALYZE failed: {exc}")
    finally:
        await close_pool(pool)

    label = "chunks parsed (dry run)" if args.dry_run else "chunks written"
    print(f"{len(files)} files, {written} {label}, {skipped} skipped, {failed} failed, "
          f"{time.monotonic() - t0:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
