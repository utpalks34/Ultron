"""Scan a local music folder and upsert tracks into registry_media.

Run with backend-ai as the working directory, because config.py reads .env relative to the cwd
(the database connection string is settings.pg_dsn, from PG_DSN in backend-ai/.env):

    cd backend-ai
    python ..\\scripts\\scan_music.py [--folder PATH] [--dry-run]
"""
import argparse
import sys
from pathlib import Path

import mutagen
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend-ai"))
from config import settings  # noqa: E402

EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav"}

UPSERT = """
INSERT INTO registry_media (title, artist, path)
VALUES (%s, %s, %s)
ON CONFLICT (path) DO UPDATE SET title = EXCLUDED.title, artist = EXCLUDED.artist
"""
# tags and embedding are deliberately absent from the DO UPDATE clause: never overwritten.
# TODO Phase 6: embed title+artist+tags via nomic-embed-text.


def read_track(path: Path):
    """Return (title, artist) or None if the file is unreadable."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return None
    if audio is None:
        return None
    tags = audio.tags or {}

    def first(key):
        values = tags.get(key)
        return str(values[0]).strip() or None if values else None

    return first("title") or path.stem, first("artist")


def main() -> int:
    parser = argparse.ArgumentParser(description="Index local music into registry_media.")
    parser.add_argument("--folder", default=settings.music_folder)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.folder:
        print("No music folder: pass --folder or set MUSIC_FOLDER in backend-ai/.env")
        return 1
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Music folder does not exist: {folder}")
        return 1

    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if not files:
        print(f"No .mp3/.flac/.m4a/.wav files found in {folder}")
        return 1

    tracks, skipped = [], 0
    for path in files:
        info = read_track(path)
        if info is None:
            skipped += 1
            continue
        tracks.append((info[0], info[1], str(path)))

    upserted = 0
    if not args.dry_run:
        # One transaction: psycopg commits when the `with` block exits cleanly and rolls back on
        # any exception, so a failed write leaves the table unchanged and upserted at 0.
        with psycopg.connect(settings.pg_dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(UPSERT, tracks)
                written = cur.rowcount
        upserted = written  # reached only after the commit succeeded

    print(f"scanned: {len(files)}  upserted: {upserted}  skipped: {skipped}")
    if args.dry_run:
        for title, artist, _ in tracks[:10]:
            print(f"  {title} / {artist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
