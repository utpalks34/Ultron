"""Endpointing and STT engine. Uses simple RMS energy detection, not Silero VAD: the
blueprint's torch.hub download needs runtime network access (breaks the offline rule) and
torch is heavy for this. RMS mirrors what useAudioLevel.js already does client-side for the
orb. If false triggers are a problem on a given mic/room, tune stt_rms_threshold and
stt_silence_ms first; Silero VAD is a documented future upgrade, not required."""
import numpy as np


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0


class Endpointer:
    """Feed it one block's RMS at a time. Pure state machine, fully testable without audio.
    speech_confirm_blocks requires several consecutive above-threshold blocks before treating
    the utterance as real speech, so a single noise spike (a click, a chair creak) cannot by
    itself trigger a transcription."""
    def __init__(self, threshold: float, silence_ms: float, block_ms: float,
                max_s: float, min_s: float, speech_confirm_blocks: int = 3):
        self.threshold, self.max_blocks = threshold, int(max_s * 1000 / block_ms)
        self.silence_blocks = max(1, int(silence_ms / block_ms))
        self.min_blocks = max(1, int(min_s * 1000 / block_ms))
        self.speech_confirm_blocks = max(1, speech_confirm_blocks)
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.heard_speech = False
        self.silence_run = 0
        self.speech_run = 0

    def feed(self, level: float) -> str:
        self.n += 1
        if level >= self.threshold:
            self.speech_run += 1
            self.silence_run = 0
            if self.speech_run >= self.speech_confirm_blocks:
                self.heard_speech = True
        else:
            self.speech_run = 0
            self.silence_run += 1
        if self.heard_speech and self.silence_run >= self.silence_blocks and self.n >= self.min_blocks:
            return "finalize"
        if self.n >= self.max_blocks:
            return "finalize" if self.heard_speech else "discard"
        return "continue"


import asyncio
import logging
import queue
import threading

import sounddevice as sd
from faster_whisper import WhisperModel

from config import settings
from sensors.audio_device import resolve_input_device_verified

log = logging.getLogger("ultron.stt")


class SttError(RuntimeError):
    pass


class SttEngine:
    """One persistent background thread: waits to be armed, captures one utterance with RMS
    endpointing, transcribes it, and hands the text to on_final on the asyncio loop via
    run_coroutine_threadsafe. Publishes state/transcript/error events itself. _busy prevents a
    second arm() from queuing a phantom capture while one is already in progress."""

    def __init__(self, bus, on_final):
        self.bus = bus
        self.on_final = on_final
        self.loop = None
        self._model = None
        self._arm_q = queue.Queue()
        self._cancel = threading.Event()
        self._busy = threading.Event()
        self._thread = None
        self.ready = False
        self.load_error = None

    def load(self) -> bool:
        """Call once, off the event loop. Never raises. local_files_only=True keeps the offline
        invariant at runtime: a missing model becomes a visible startup warning instead of a
        silent network download. Pre-download once with scripts/pull_whisper_model.py."""
        try:
            self._model = WhisperModel(settings.stt_model, device="cpu",
                                       compute_type=settings.stt_compute_type,
                                       cpu_threads=settings.stt_cpu_threads,
                                       download_root=settings.stt_download_root,
                                       local_files_only=True)
            self.ready = True
        except Exception as exc:
            self.load_error = (f"{exc.__class__.__name__}: {str(exc)[:200]} - the model is not "
                              "cached locally; run 'python scripts\\pull_whisper_model.py' once "
                              "with internet access, then restart the backend")
            log.warning("whisper load failed: %s", exc)
        return self.ready

    def start(self, loop) -> None:
        self.loop = loop
        print("!!! DEBUG: SttEngine.start() called, thread about to spawn", flush=True)
        self._thread = threading.Thread(target=self._run, name="ultron-stt", daemon=True)
        self._thread.start()

    def arm(self) -> bool:
        if not self.ready or self._busy.is_set():
            print(f"!!! DEBUG: arm() returning False, ready={self.ready} busy={self._busy.is_set()}", flush=True)
            return False
        self._busy.set()
        self._cancel.clear()
        self._arm_q.put(True)
        print(f"!!! DEBUG: arm() returning True, ready={self.ready} busy={self._busy.is_set()}", flush=True)
        return True

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def busy(self) -> bool:
        """True from arm() until the utterance has been captured, transcribed and handed on."""
        return self._busy.is_set()

    def report(self, source: str, message: str) -> None:
        """Thread-safe warn `error` event for sibling sensors (the wake word listener)."""
        if self.loop is not None:
            self._publish({"type": "error", "payload": {"severity": "warn",
                                                         "source": source, "message": message}})

    def _publish(self, evt: dict) -> None:
        asyncio.run_coroutine_threadsafe(self.bus.publish(evt), self.loop)

    def _log_on_final_error(self, fut) -> None:
        exc = fut.exception()
        if exc is not None:
            log.warning("on_final handler failed: %s", exc)

    def _run(self) -> None:
        print("!!! DEBUG: SttEngine thread actually running now", flush=True)
        block = max(1, int(settings.stt_sample_rate * settings.stt_block_ms / 1000))
        while True:
            print("!!! DEBUG: waiting on _arm_q.get()", flush=True)
            self._arm_q.get()
            print("!!! DEBUG: _arm_q.get() returned, starting capture", flush=True)
            try:
                self._publish({"type": "state", "payload": {"phase": "listening"}})
                try:
                    audio = self._capture(block)
                except Exception as exc:
                    log.warning("capture failed: %s", exc)
                    self._publish({"type": "error", "payload": {"severity": "warn",
                        "source": "stt", "message": f"microphone capture failed: {exc}"}})
                    audio = None
                if audio is None:
                    self._publish({"type": "state", "payload": {"phase": "idle"}})
                    continue
                self._publish({"type": "state", "payload": {"phase": "thinking"}})
                try:
                    text = self._transcribe(audio)
                except Exception as exc:
                    log.warning("transcribe failed: %s", exc)
                    self._publish({"type": "error", "payload": {"severity": "warn",
                        "source": "stt", "message": f"transcription failed: {exc}"}})
                    self._publish({"type": "state", "payload": {"phase": "idle"}})
                    continue
                log.info("heard: %r", text)
                self._publish({"type": "transcript", "payload": {"text": text, "final": True}})
                fut = asyncio.run_coroutine_threadsafe(self.on_final(text), self.loop)
                fut.add_done_callback(self._log_on_final_error)
            finally:
                self._busy.clear()

    def _capture(self, block: int):
        ep = Endpointer(settings.stt_rms_threshold, settings.stt_silence_ms,
                        settings.stt_block_ms, settings.stt_max_utterance_s,
                        settings.stt_min_utterance_s, settings.stt_speech_confirm_blocks)
        chunks = []
        peak = 0.0
        with sd.InputStream(samplerate=settings.stt_sample_rate, channels=1,
                            dtype="float32", blocksize=block,
                            device=resolve_input_device_verified(settings.stt_input_device,
                                                                 settings.stt_input_device_exclude)
                            ) as stream:
            print("!!! DEBUG: InputStream opened, entering read loop", flush=True)
            while True:
                if self._cancel.is_set():
                    return None
                data, _ = stream.read(block)
                mono = data[:, 0]
                chunks.append(mono.copy())
                level = rms(mono)
                peak = max(peak, level)
                outcome = ep.feed(level)
                if outcome == "discard":
                    log.info("capture discarded: no speech (peak rms %.4f, threshold %.4f) - "
                             "raise the mic level or lower STT_RMS_THRESHOLD", peak,
                             settings.stt_rms_threshold)
                    return None
                if outcome == "finalize":
                    log.info("capture finalized: %.1fs, peak rms %.4f, threshold %.4f",
                             len(chunks) * settings.stt_block_ms / 1000, peak,
                             settings.stt_rms_threshold)
                    return np.concatenate(chunks)

    def _transcribe(self, audio) -> str:
        segments, _ = self._model.transcribe(audio, language="en", beam_size=1, vad_filter=False)
        return "".join(s.text for s in segments).strip()
