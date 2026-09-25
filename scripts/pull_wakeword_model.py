"""Run once, with internet access, to cache openwakeword's pretrained models locally
(including hey_jarvis). After this the wake word engine loads offline.

    python ..\\scripts\\pull_wakeword_model.py
"""
import sys


def main() -> int:
    try:
        import openwakeword.utils
        print("downloading openwakeword pretrained models ...")
        openwakeword.utils.download_models()
    except Exception as exc:
        print(f"download failed: {exc.__class__.__name__}: {exc}")
        return 1
    print("done - wake word detection can now load offline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
