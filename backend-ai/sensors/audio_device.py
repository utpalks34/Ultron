"""Resolves a microphone by name substring instead of a numeric index, because Windows
renumbers device indices whenever anything is plugged or unplugged."""
import logging
import time

import sounddevice as sd

log = logging.getLogger("ultron.audio_device")


def resolve_input_device(name_substring: str | None, exclude_substring: str) -> int | None:
    """Returns a device index, or None to let sounddevice use its own current default
    (which itself follows Windows' active default mic, and is usually correct)."""
    if not name_substring:
        return None
    exclude = exclude_substring.lower()
    wanted = name_substring.lower()
    for idx, dev in enumerate(sd.query_devices()):
        name = dev["name"].lower()
        if dev["max_input_channels"] > 0 and wanted in name and exclude not in name:
            return idx
    return None   # fall back to system default rather than crash


def resolve_input_device_verified(name_substring: str | None, exclude_substring: str,
                                  retries: int = 2, retry_delay: float = 0.5) -> int | None:
    """Like resolve_input_device, but actually tries to open a tiny test stream on the
    resolved device before returning it, retrying (with a fresh device query each time,
    since indices can change between attempts) if that fails. Falls back to letting
    sounddevice choose its own default rather than raising, so callers always get SOME
    value back."""
    if not name_substring:
        return None
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        idx = None
        try:
            idx = resolve_input_device(name_substring, exclude_substring)
            if idx is not None:
                # a real open/close: a device can be listed yet unopenable right after a
                # Bluetooth / USB connect or disconnect
                stream = sd.InputStream(device=idx, channels=1, samplerate=16000, blocksize=320)
                stream.close()
                return idx
        except Exception as exc:
            log.warning("input device %r (index %s) failed to open, attempt %d/%d: %s",
                        name_substring, idx, attempt, attempts, exc)
        else:
            if idx is None:
                log.warning("input device %r not found, attempt %d/%d",
                            name_substring, attempt, attempts)
        if attempt < attempts:
            time.sleep(retry_delay)
    log.warning("input device %r unusable after %d attempts; using the system default",
                name_substring, attempts)
    return None
