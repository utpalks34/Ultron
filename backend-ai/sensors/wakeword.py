"""Continuous wake-word listener. Runs its own InputStream, but only while SttEngine is idle:
on detection it calls SttEngine.arm() - identical to a hotkey press - and releases the
microphone until the capture is finished, so two streams never hold the device at once. The
rest of the voice pipeline (endpointing, transcription, run start) is unchanged and reused."""
import numpy as np


def above_threshold(score: float, threshold: float) -> bool:
    return score >= threshold


def apply_gain(samples: np.ndarray, target_rms: float = 0.06, max_gain: float = 6.0,
               gate_rms: float = 0.004) -> np.ndarray:
    """Lift a quiet int16 block toward target_rms so the wake word model, which was trained on
    normal-level speech, still scores it. Blocks below gate_rms (room noise) and blocks that
    are already loud enough pass through untouched."""
    if len(samples) == 0:
        return samples
    level = float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
    if level < gate_rms or level >= target_rms:
        return samples
    gain = min(max_gain, target_rms / level)
    return np.clip(samples.astype(np.float32) * gain, -32768, 32767).astype(np.int16)


# ── Impure part: threading, sounddevice, openwakeword ───────────────────────
import logging
import threading
import time

import sounddevice as sd

from config import settings
from sensors.audio_device import resolve_input_device_verified

log = logging.getLogger("ultron.wakeword")

_CHUNK = 1280            # openwakeword expects 80 ms @ 16 kHz mono
_LOG_SCORE_FROM = 0.15   # scores below this are noise; above it they help tune wake_word_threshold


class WakeWordError(RuntimeError):
    pass


class WakeWordEngine:
    def __init__(self, stt_engine):
        self.stt = stt_engine
        self._model = None
        self._thread = None
        self._stop = threading.Event()
        self._last_trigger = 0.0
        self.ready = False
        self.load_error: str | None = None

    def load(self) -> bool:
        """Call once, off the event loop. Never raises."""
        try:
            from openwakeword.model import Model
            # onnx explicitly: the default is tflite, which is not installed on Windows
            self._model = Model(wakeword_models=[settings.wake_word_model],
                                inference_framework="onnx")
            self.ready = True
        except Exception as exc:
            self.load_error = (f"{exc.__class__.__name__}: {str(exc)[:200]} - run "
                              "'python ..\\scripts\\pull_wakeword_model.py' once with "
                              "internet access")
            log.warning("wake word model load failed: %s", exc)
        return self.ready

    def start(self) -> None:
        if not self.ready or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ultron-wakeword", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _wait_stt_idle(self) -> None:
        while not self._stop.is_set() and self.stt.busy:
            time.sleep(0.1)

    def _run(self) -> None:
        reported = False
        while not self._stop.is_set():
            self._wait_stt_idle()
            if self._stop.is_set():
                return
            # the previous phrase (and any speech captured meanwhile) must not leak into the
            # next detection window
            self._model.reset()
            try:
                self._listen()
                reported = False
            except Exception as exc:
                log.warning("wake word listener error: %s", exc)
                if not reported:
                    reported = True
                    self.stt.report("wakeword", f"wake word listener stopped ({exc}); retrying")
                self._stop.wait(3.0)

    def _listen(self) -> None:
        """Holds the microphone until stop, until STT needs it, or until the wake word fires."""
        last_log = 0.0
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=_CHUNK,
                            device=resolve_input_device_verified(settings.stt_input_device,
                                                                 settings.stt_input_device_exclude)
                            ) as stream:
            log.info("wake word listening for %r (threshold %.2f)", settings.wake_word_model,
                     settings.wake_word_threshold)
            while not self._stop.is_set():
                if self.stt.busy:          # hotkey / orb click armed STT: give it the device
                    return
                data, _ = stream.read(_CHUNK)
                preds = self._model.predict(apply_gain(data[:, 0]))
                # the key is the name passed to Model(); fall back to the best score so a
                # differently keyed openwakeword version cannot leave this at zero forever
                score = float(preds.get(settings.wake_word_model,
                                        max(preds.values(), default=0.0)))
                now = time.monotonic()
                if score >= _LOG_SCORE_FROM and now - last_log >= 0.5:
                    last_log = now
                    log.info("wake word score %.2f (threshold %.2f)", score,
                             settings.wake_word_threshold)
                if (above_threshold(score, settings.wake_word_threshold)
                        and now - self._last_trigger >= settings.wake_word_cooldown_s):
                    self._last_trigger = now
                    log.info("wake word detected (score=%.2f)", score)
                    self.stt.arm()         # identical to a hotkey press
                    return                 # closes the stream before STT opens its own
