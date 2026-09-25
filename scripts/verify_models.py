"""Presence and sanity check of the local model setup. Read-only; downloads nothing.

Run from anywhere:  python scripts\verify_models.py
Ollama's blob store is not a stable public layout, so this does not checksum anything: an
Ollama model passes when `ollama show <model>` exits 0, the Piper voice passes when the file
exists and is over 1 MB (a partial download is usually far smaller), and the faster-whisper
cache passes when it holds at least one file. Exits 1 if anything is missing."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend-ai"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)          # config.py reads .env, and tts_model_path is relative, from here

from config import settings  # noqa: E402

MIN_VOICE_BYTES = 1_000_000


def check_ollama(model: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(["ollama", "show", model], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, "ollama is not on PATH"
    except subprocess.TimeoutExpired:
        return False, "ollama show timed out"
    if proc.returncode == 0:
        return True, "ollama show ok"
    return False, (proc.stderr.strip() or "ollama show failed")[:80]


def check_voice() -> tuple[bool, str]:
    path = Path(settings.tts_model_path)
    if not path.is_file():
        return False, f"missing: {path.resolve()}"
    size = path.stat().st_size
    if size <= MIN_VOICE_BYTES:
        return False, f"only {size} bytes (partial download?)"
    return True, f"{size / 1e6:.1f} MB"


def whisper_cache_dirs() -> list[Path]:
    if settings.stt_download_root:
        return [Path(settings.stt_download_root)]
    hub = os.environ.get("HF_HUB_CACHE")
    if not hub:
        home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        hub = str(Path(home) / "hub")
    # the library default: one HF cache folder per model, e.g. models--Systran--faster-whisper-base.en
    return sorted(Path(hub).glob(f"models--*faster-whisper-{settings.stt_model}*"))


def check_whisper() -> tuple[bool, str]:
    for root in whisper_cache_dirs():
        if root.is_dir():
            n = sum(1 for p in root.rglob("*") if p.is_file())
            if n:
                return True, f"{n} files in {root}"
    where = settings.stt_download_root or "the default Hugging Face cache"
    return False, f"nothing cached for {settings.stt_model} in {where}"


def main() -> int:
    rows = []
    for model in (settings.router_model, settings.embed_model, settings.worker_model):
        ok, note = check_ollama(model)
        rows.append((f"ollama: {model}", ok, note, f"ollama pull {model}"))
    ok, note = check_voice()
    rows.append((f"piper voice: {settings.tts_model_path}", ok, note,
                 "python scripts\\pull_piper_voice.py"))
    ok, note = check_whisper()
    rows.append((f"whisper: {settings.stt_model}", ok, note,
                 "python scripts\\pull_whisper_model.py"))

    width = max(len(r[0]) for r in rows)
    for name, ok, note, _ in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {note}")
    failed = [r for r in rows if not r[1]]
    if failed:
        print("\nFix with:")
        for name, _, _, fix in failed:
            print(f"  {fix}    # {name}")
        return 1
    print("\nAll present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
