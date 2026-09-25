"""Music: local library first (registry_media), then YouTube through yt-dlp, played by ONE
long-lived mpv process controlled over a Windows named pipe (JSON IPC).

The pure part (PlayRequest, parse_play) is unit-tested offline. The IO part blocks on the pipe, so
every call from async code goes through asyncio.to_thread; the plain get/set/control functions are
blocking and must be wrapped the same way by their callers. Nothing here asks for confirmation:
callers go through tools.registry.gate.
"""
import asyncio
import itertools
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import settings
from memory.embedder import EmbedError, embed_query
from memory.vectorstore import vec_literal
from tools.connectivity import online
from tools.resolve import resolve

log = logging.getLogger("ultron.music")

PIPE = r"\\.\pipe\ultron-mpv"
MAX_REPLY_LINES = 20        # mpv also emits event lines; give up after this many
REPLY_TIMEOUT_S = 3.0       # deadline for mpv's reply to one command
MAX_QUEUE = 200             # a shuffle of a huge library is capped
ERROR_PIPE_BUSY = 231
_COLS = "id, title, artist, path, tags"       # never SELECT the embedding column for playback


class MusicError(RuntimeError):
    pass


class MusicOffline(MusicError):
    """Online playback is unavailable (disabled or no internet), as opposed to having failed."""


# --------------------------------------------------------------------------- pure

@dataclass
class PlayRequest:
    mode: str                   # "favourites" | "any" | "query"
    query: str = ""
    source: str = "auto"        # "auto" | "online" | "local"


_VERB = re.compile(
    r"^(?:(?:please|can you|could you|would you)\s+)?(?:play|put on|start playing|queue(?:\s+up)?)\b\s*")
_ONLINE = re.compile(r"\bon\s+youtube(?:\s+music)?\b|\byoutube\s+music\b")
_LOCAL = re.compile(
    r"\bfrom\s+(?:my\s+|the\s+)?(?:library|folder|laptop|computer|pc|local)\b"
    r"(?:\s+(?:files|music|library|folder))?")
_FAVOURITE = re.compile(r"\bfavou?rites?\b")
_FILLER = re.compile(r"\b(?:the\s+song|the\s+track|some|songs?|tracks?|music|please)\b")
_LEADING = re.compile(r"^my\b\s*")          # "the" stays: "The Weeknd" is a real title


def parse_play(text: str) -> PlayRequest:
    t = re.sub(r"[^\w\s'-]", " ", text.lower())
    t = " ".join(t.split())

    online_src = bool(_ONLINE.search(t))
    local_src = bool(_LOCAL.search(t))
    t = _LOCAL.sub(" ", _ONLINE.sub(" ", t))
    favourite = bool(_FAVOURITE.search(t))
    t = _FAVOURITE.sub(" ", t)

    t = _VERB.sub("", " ".join(t.split()))
    t = " ".join(_FILLER.sub(" ", t).split())
    prev = None
    while prev != t:
        prev = t
        t = _LEADING.sub("", t)

    source = "online" if online_src else "local" if local_src else "auto"
    mode = "favourites" if favourite else "any" if not t else "query"
    return PlayRequest(mode=mode, query=t, source=source)


# --------------------------------------------------------------------------- mpv pipe (blocking)

_lock = threading.Lock()
_ids = itertools.count(1)
_ducked_from: float | None = None
_duck_lock = threading.Lock()


def _pipe_open() -> bool:
    try:
        with open(PIPE, "r+b", buffering=0):
            return True
    except OSError as exc:
        return getattr(exc, "winerror", None) == ERROR_PIPE_BUSY    # busy means it exists


def ensure_player() -> None:
    """Start the single long-lived mpv if its control pipe is not there."""
    if _pipe_open():
        return
    yt_dlp = shutil.which("yt-dlp") or str(Path(sys.executable).with_name("yt-dlp.exe"))
    args = [settings.mpv_path, "--idle=yes", "--force-window=no", "--no-video",
            f"--input-ipc-server={PIPE}", f"--script-opts=ytdl_hook-ytdl_path={yt_dlp}"]
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    except FileNotFoundError as exc:
        raise MusicError("mpv not found - install mpv and put it on PATH") from exc
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if _pipe_open():
            return
        time.sleep(0.1)
    raise MusicError("mpv started but its control pipe did not appear")


def _pipe_available(f) -> int:
    """Bytes ready to read on the pipe, without blocking (PeekNamedPipe)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    peek = ctypes.windll.kernel32.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD,
                     wintypes.LPDWORD, wintypes.LPDWORD]
    peek.restype = wintypes.BOOL
    avail = wintypes.DWORD(0)
    if not peek(msvcrt.get_osfhandle(f.fileno()), None, 0, None, ctypes.byref(avail), None):
        raise ctypes.WinError()        # e.g. broken pipe: mpv exited
    return avail.value


def _request(cmd: list) -> dict:
    rid = next(_ids)
    line = json.dumps({"command": cmd, "request_id": rid}).encode("utf-8") + b"\n"
    with _lock:
        try:
            with open(PIPE, "r+b", buffering=0) as f:
                f.write(line)
                deadline = time.monotonic() + REPLY_TIMEOUT_S
                buf = b""
                lines = 0
                while lines < MAX_REPLY_LINES:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        # never block in read(): poll first, so a silent mpv cannot hang the caller
                        avail = _pipe_available(f)
                        if avail == 0:
                            if time.monotonic() >= deadline:
                                raise MusicError("mpv did not respond in time")
                            time.sleep(0.01)
                            continue
                        chunk = f.read(min(avail, 65536))
                        if not chunk:
                            break
                        buf += chunk
                        continue
                    raw, buf = buf[:nl], buf[nl + 1:]
                    lines += 1
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if msg.get("request_id") == rid:
                        return msg
        except FileNotFoundError as exc:
            raise MusicError("mpv is not running - nothing is playing") from exc
        except OSError as exc:
            raise MusicError(f"cannot talk to mpv ({exc.__class__.__name__})") from exc
    raise MusicError("no reply from mpv")


def _send(cmd: list):
    """One command; returns the reply's data."""
    reply = _request(cmd)
    if reply.get("error") != "success":
        raise MusicError(f"mpv: {reply.get('error')}")
    return reply.get("data")


def get(prop: str):
    """A property's value, or None when mpv has none (for example nothing is loaded)."""
    reply = _request(["get_property", prop])
    return reply.get("data") if reply.get("error") == "success" else None


def set(prop: str, value) -> None:  # noqa: A001  (the name is part of the Part 6.5 interface)
    _send(["set_property", prop, value])


def pause() -> None:
    set("pause", True)


def resume() -> None:
    set("pause", False)


def next_track() -> None:
    _send(["playlist-next"])


def previous_track() -> None:
    _send(["playlist-prev"])


def stop() -> None:
    _send(["stop"])


def set_volume(pct: float) -> None:
    set("volume", max(0, min(100, pct)))


def _player_running() -> bool:
    return _pipe_open()


def duck(pct: float = 25) -> bool:
    """Lower mpv's volume while TTS speaks, remembering the previous level. No-op if mpv is off.
    Returns True when the volume was lowered, so the caller knows to un-duck."""
    global _ducked_from
    with _duck_lock:
        if not _player_running():
            return False
        if _ducked_from is None:
            current = get("volume")
            _ducked_from = float(current) if current is not None else 100.0
        set_volume(pct)
        return True


def unduck() -> None:
    global _ducked_from
    with _duck_lock:
        if _ducked_from is None:
            return
        previous, _ducked_from = _ducked_from, None
        if _player_running():
            set_volume(previous)


# --------------------------------------------------------------------------- playback

def _load_paths(paths: list[str]) -> None:
    ensure_player()
    for i, path in enumerate(paths):
        _send(["loadfile", path, "replace" if i == 0 else "append"])
    set("pause", False)


async def play_path(path: str) -> None:
    if not os.path.isfile(path):
        raise MusicError(f"file is missing: {path}")
    await asyncio.to_thread(_load_paths, [path])


async def play_list(paths: list[str]) -> int:
    """First track replaces the queue, the rest are appended. Returns how many were queued."""
    existing = [p for p in paths if os.path.isfile(p)]
    if not existing:
        raise MusicError("none of those files exist any more - rescan the library "
                         "(scripts\\scan_music.py)")
    await asyncio.to_thread(_load_paths, existing)
    return len(existing)


async def favourites(pool) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_COLS} FROM registry_media WHERE 'favourite' = ANY(tags) ORDER BY title")
    return [dict(r) for r in rows]


async def library(pool) -> list[dict]:
    rows = await pool.fetch(f"SELECT {_COLS} FROM registry_media ORDER BY title")
    return [dict(r) for r in rows]


async def find_local(pool, phrase: str) -> tuple[str, list[dict]] | None:
    """Fuzzy match on title / artist / tags, then a mood fallback on embeddings. None if neither
    qualifies."""
    decision, rows = await resolve(pool, "media", phrase)
    if rows and rows[0]["score"] >= settings.music_local_min_score:
        return decision, rows

    try:
        vec = await embed_query(phrase)
    except EmbedError as exc:      # the mood fallback is optional; playback can still go online
        log.warning("mood search skipped: %s", exc)
        return None
    row = await pool.fetchrow(
        f"SELECT {_COLS}, 1 - (embedding <=> $1::text::vector) AS sim FROM registry_media "
        "WHERE embedding IS NOT NULL ORDER BY embedding <=> $1::text::vector LIMIT 1",
        vec_literal(vec))
    if row is not None and row["sim"] >= settings.media_embed_min:
        return "one", [dict(row)]
    return None


def _load_online(url: str) -> None:
    ensure_player()
    _send(["loadfile", url, "replace"])
    set("pause", False)


async def play_online(query: str) -> str:
    """YouTube via yt-dlp. Returns the media title. Fails visibly."""
    if not settings.online_music_enabled:
        raise MusicOffline("online music is disabled")
    if not await online():
        raise MusicOffline("no internet connection")
    query = " ".join(query.split())
    await asyncio.to_thread(_load_online, f"ytdl://ytsearch1:{query} official audio")

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        idle = await asyncio.to_thread(get, "idle-active")
        title = await asyncio.to_thread(get, "media-title")
        if idle is False and title and not str(title).startswith("ytdl://"):
            return str(title)
    raise MusicError("online playback failed - update yt-dlp (pip install -U yt-dlp); recent "
                     "YouTube changes may also need a JavaScript runtime such as deno")


def _label(row: dict) -> str:
    return f"{row['title']} - {row['artist']}" if row.get("artist") else row["title"]


async def play(ctx, pool, req: PlayRequest) -> str:
    """Route a play request and answer in one sentence. Never guesses."""
    async def note(tool: str, preview: str) -> None:
        if ctx is not None:
            await ctx.emit(tool, "done", preview)

    async def audit_online(query: str) -> None:
        # play_online is the documented second network-egress exception and must be audited,
        # even though play_music itself is only "navigate" risk
        if ctx is not None:
            await ctx.audit("play_online", query)

    if req.source == "online":
        if not req.query:
            return "Tell me what to play on YouTube."
        await note("play_online", req.query)
        await audit_online(req.query)
        return f"Playing {await play_online(req.query)} from YouTube"

    if req.mode in ("favourites", "any"):
        rows = await favourites(pool)
        what = "your favourites"
        if not rows and req.mode == "any":
            rows = await library(pool)
            what = "your library"
        if not rows:
            if req.mode == "favourites":
                return "None of your tracks is tagged 'favourite' yet."
            return "Your music library is empty - run scripts\\scan_music.py."
        random.shuffle(rows)
        rows = rows[:MAX_QUEUE]
        await note("play_local", f"{what}, {len(rows)} tracks")
        count = await play_list([r["path"] for r in rows])
        return f"Playing a shuffle of {what} ({count} tracks)"

    found = await find_local(pool, req.query)
    await note("find_local", f"{req.query!r} -> {found[0] if found else 'none'}")
    if found:
        decision, rows = found
        if decision == "one":
            await play_path(rows[0]["path"])
            return f"Playing {_label(rows[0])} (local)"
        options = "; ".join(_label(r) for r in rows[:3])
        return f"I found several matches: {options}. Which one do you want?"

    if req.source == "local":
        return f"I couldn't find {req.query!r} in your library."
    try:
        await note("play_online", req.query)
        await audit_online(req.query)
        return f"Playing {await play_online(req.query)} from YouTube"
    except MusicOffline as exc:
        # the message says whether online music is disabled or there is no connection
        return f"I couldn't find that in your library, and {exc}."
