"""Standalone TTS diagnosis: loads the Piper voice and speaks one text straight through
sounddevice, without the backend, the event loop or the bus (no audio_level events).

Run from backend-ai/ with the root venv active:
    python ..\\scripts\\test_tts.py "hello, this is a test"
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello, I am online and ready"
    try:
        import sounddevice as sd

        from config import settings
        import sensors.tts as tts

        model_path = Path(settings.tts_model_path)
        print(f"tts_model_path: {settings.tts_model_path}")
        print(f"exists: {model_path.is_file()}")

        ok = tts.load()
        print(f"tts.load(): {ok}")
        if not ok:
            print(f"error: {tts.error()}")
            return 1

        stream = None
        try:
            for pcm, sr in tts._iter_pcm(tts._voice, text):
                if stream is None:
                    print(f"sample rate: {sr}")
                    stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16",
                                             device=settings.tts_output_device)
                    stream.start()
                stream.write(pcm)
        finally:
            if stream is not None:
                stream.stop()
                stream.close()

        if stream is None:
            print("no audio was produced")
            return 1
        print("TTS TEST OK")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
