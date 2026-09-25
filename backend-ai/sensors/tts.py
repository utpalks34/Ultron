"""Piper TTS with barge-in. _current tracks the one active speech task so a new voice/text
turn can interrupt it before starting; see barge_in()."""
import re

_MD = re.compile(r"[*_`#]|\[(.*?)\]\([^)]*\)")
_SOURCES = re.compile(r"\n\nSources?:.*$", re.S)


def strip_for_speech(text: str, max_chars: int) -> str:
    """Drop markdown emphasis, footer citations, and long code fences; keep link text."""
    text = _SOURCES.sub("", text)
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = _MD.sub(lambda m: m.group(1) or "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        cut = text.rfind(". ", 0, max_chars)
        text = text[: cut + 1] if cut > max_chars * 0.5 else text[:max_chars]
        text += " ... I've kept that short; ask me to elaborate if you want more."
    return text


# ── Impure part: threading, sounddevice, Piper ──────────────────────────────
import asyncio
import logging
import sys
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import settings
from tools import music

log = logging.getLogger("ultron.tts")

_SLICE_MS = 30

_voice = None
_voice_error: str | None = None


class TtsError(RuntimeError):
    pass


class TtsHandle:
    def __init__(self, task: asyncio.Task, stop_event: threading.Event):
        self.task = task
        self.stop = stop_event


_current: TtsHandle | None = None
_lock = asyncio.Lock()   # serialises reads/writes of _current across concurrent speak() calls


def load() -> bool:
    """Call once, off the event loop. Never raises; failure is reported via ready()/error()."""
    global _voice, _voice_error
    path = Path(settings.tts_model_path)
    if not path.is_file():
        _voice_error = (f"{path} not found - run "
                        "'python ..\\scripts\\pull_piper_voice.py' from backend-ai/")
        return False
    try:
        from piper import PiperVoice
        _voice = PiperVoice.load(str(path))
        return True
    except Exception as exc:
        _voice_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
        log.warning("piper load failed: %s", exc)
        return False


def ready() -> bool:
    return _voice is not None


def error() -> str | None:
    return _voice_error


async def barge_in() -> None:
    """Stop any in-progress speech and wait for its cleanup (duck undone, phase reset) to
    finish before returning. Idempotent and safe to call when nothing is speaking."""
    async with _lock:
        handle = _current
        if handle is None or handle.task.done() or handle.stop.is_set():
            return
        handle.stop.set()
        handle.task.cancel()
    try:
        await handle.task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass   # the task's own except/finally already logged and cleaned up


def _iter_pcm(voice, text: str):
    """Yields (int16 numpy array, sample_rate) regardless of the installed piper-tts
    version's exact API shape. Tries each known shape in order and uses whichever works."""
    # Shape 1: voice.synthesize(text) yields objects with .audio_int16_array/.sample_rate
    try:
        started = False
        for chunk in voice.synthesize(text):
            if not started:
                print("using shape: synthesize()", file=sys.stderr)
                started = True
            if hasattr(chunk, "audio_int16_array"):
                yield chunk.audio_int16_array, chunk.sample_rate
                continue
            if hasattr(chunk, "audio_float_array"):
                pcm = (chunk.audio_float_array * 32767).astype(np.int16)
                yield pcm, chunk.sample_rate
                continue
            raise AttributeError(f"unrecognized chunk shape from synthesize(): {type(chunk)}")
        return
    except AttributeError:
        pass
    except TypeError:
        pass
    # Shape 2: voice.synthesize_stream_raw(text) yields raw int16 PCM bytes
    if hasattr(voice, "synthesize_stream_raw"):
        sr = getattr(getattr(voice, "config", None), "sample_rate", 22050)
        started = False
        for raw in voice.synthesize_stream_raw(text):
            if not started:
                print("using shape: synthesize_stream_raw()", file=sys.stderr)
                started = True
            yield np.frombuffer(raw, dtype=np.int16), sr
        return
    raise TtsError("no compatible piper-tts API found (tried synthesize() and "
                   "synthesize_stream_raw()) - report `pip show piper-tts` output")


def _speak_blocking(bus, loop, text: str, stop: threading.Event) -> None:
    """Runs in a worker thread. _iter_pcm() hides which piper-tts API shape is installed; each
    PCM chunk (usually one sentence) is re-sliced into ~30ms pieces here so audio_level updates
    smoothly and stop is checked well within one sentence, not just between sentences."""
    stream = None
    slice_frames = None
    try:
        for pcm, sr in _iter_pcm(_voice, text):
            if stop.is_set():
                break
            if stream is None:
                stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16",
                                         device=settings.tts_output_device)
                stream.start()
                slice_frames = max(1, int(sr * _SLICE_MS / 1000))
            for i in range(0, len(pcm), slice_frames):
                if stop.is_set():
                    break
                piece = pcm[i:i + slice_frames]
                if len(piece) == 0:
                    continue
                stream.write(piece)
                level = float(np.sqrt(np.mean((piece.astype(np.float32) / 32768.0) ** 2)))
                asyncio.run_coroutine_threadsafe(
                    bus.publish({"type": "audio_level", "payload": {"rms": min(1.0, level)}}),
                    loop)
            if stop.is_set():
                break
    finally:
        if stream is not None:
            stream.stop()
            stream.close()


async def speak(bus, set_phase, run_id: str | None, text: str) -> None:
    """MUST be launched via asyncio.create_task by the caller, never awaited inline, so a long
    answer never blocks run_graph or a new run from starting. Interrupts any speech already in
    progress before claiming _current, so two overlapping speak() calls (e.g. /debug/speak
    fired twice) no longer play on top of each other."""
    global _current
    if not settings.voice_enabled or not ready():
        return
    text = strip_for_speech(text, settings.tts_max_chars)
    if not text:
        return

    await barge_in()

    this_task = asyncio.current_task()
    stop = threading.Event()
    async with _lock:
        _current = TtsHandle(this_task, stop)

    ducked = False
    try:
        await set_phase("speaking", run_id)
        try:
            ducked = await asyncio.to_thread(music.duck, settings.tts_duck_pct)
        except Exception:
            log.warning("duck failed (continuing without ducking)", exc_info=True)
            ducked = False
        await asyncio.to_thread(_speak_blocking, bus, asyncio.get_running_loop(), text, stop)
    except asyncio.CancelledError:
        stop.set()
        raise
    except Exception as exc:
        log.warning("tts failed: %s", exc)
        evt = {"type": "error", "payload": {"severity": "warn", "source": "tts",
                                            "message": f"speech failed: {exc}"}}
        if run_id:
            evt["run_id"] = run_id
        await bus.publish(evt)
    finally:
        if ducked:
            try:
                await asyncio.to_thread(music.unduck)
            except Exception:
                log.warning("unduck failed", exc_info=True)
        async with _lock:
            if _current is not None and _current.task is this_task:
                _current = None
        await set_phase("idle", run_id)
