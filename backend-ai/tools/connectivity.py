"""Cached online/offline probe."""
import asyncio
import time

from config import settings

_cache: tuple[float, bool] | None = None
TTL = 30.0


async def online(force: bool = False) -> bool:
    # This probe is the only network egress outside the browser allowlist, and it opens a
    # TCP connection without sending any data.
    global _cache
    if not force and _cache is not None and time.monotonic() - _cache[0] < TTL:
        return _cache[1]
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.connectivity_probe_host,
                                    settings.connectivity_probe_port), 1.5)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        result = True
    except Exception:
        result = False
    _cache = (time.monotonic(), result)
    return result
