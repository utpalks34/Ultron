"""Downloads the faster-whisper model into the local cache. Run once, with internet access, so
sensors/stt.py can then load it with local_files_only=True at runtime (the backend itself never
touches the network). Can be run from anywhere: it adds backend-ai to sys.path itself.

    python scripts\\pull_whisper_model.py
    python scripts\\pull_whisper_model.py --model base.en
    python scripts\\pull_whisper_model.py --download-root D:\\models\\whisper

This is one of the scripts that touches the network, like pull_piper_voice.py and pull_models.py.
"""
import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

from config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Download a faster-whisper model into the local cache.")
    ap.add_argument("--model", default=settings.stt_model,
                    help="model name (default: stt_model from config)")
    ap.add_argument("--download-root", default=settings.stt_download_root,
                    help="cache folder (default: stt_download_root from config, else the library default)")
    args = ap.parse_args()

    try:
        from faster_whisper import WhisperModel
        print(f"downloading {args.model}...")
        WhisperModel(args.model, device="cpu", compute_type=settings.stt_compute_type,
                     download_root=args.download_root, local_files_only=False)
    except Exception as exc:
        print(f"FAILED: {exc.__class__.__name__}: {exc}")
        return 1

    print("done - sensors/stt.py can now load this with local_files_only=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
