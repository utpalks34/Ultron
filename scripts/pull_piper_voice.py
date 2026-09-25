"""Downloads a Piper voice (the .onnx model and its .onnx.json config) with the standard library
only. Run from backend-ai/ so the default output folder matches tts_model_path in config.py:

    python ..\\scripts\\pull_piper_voice.py
    python ..\\scripts\\pull_piper_voice.py --voice en_US-lessac-medium
    python ..\\scripts\\pull_piper_voice.py --url https://.../en_GB-alba-medium.onnx

Piper voices are hosted at huggingface.co/rhasspy/piper-voices. The URL pattern below is
believed correct as of writing; if it 404s, browse that repo for the right path and pass --url
(the .onnx.json is fetched from the same URL plus ".json"):

    https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{lang}_{COUNTRY}/{name}/{quality}/{voice}.onnx

Voice names look like "xx_XX-name-quality", for example "en_GB-alba-medium".
This is the one script that touches the network; the backend itself never does.
"""
import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

from config import settings  # noqa: E402

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
TIMEOUT_S = 30
CHUNK = 1 << 16


def derive_url(voice: str) -> str:
    try:
        locale, name, quality = voice.split("-")
        lang, country = locale.split("_")
    except ValueError:
        raise SystemExit(f"voice {voice!r} does not look like 'xx_XX-name-quality' "
                         f"(for example en_GB-alba-medium)")
    return f"{BASE}/{lang}/{lang}_{country}/{name}/{quality}/{voice}.onnx"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    print(f"GET {url}")
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done / 1e6:6.1f} / {total / 1e6:.1f} MB "
                      f"({100 * done // total}%)", end="", flush=True)
            else:
                print(f"\r  {dest.name}: {done / 1e6:6.1f} MB", end="", flush=True)
    part.replace(dest)
    print(f"\n  saved {dest}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download a Piper voice from Hugging Face.")
    ap.add_argument("--voice", default=settings.tts_voice,
                    help="voice name, xx_XX-name-quality (default: tts_voice from config)")
    ap.add_argument("--out-dir", default="models/piper/", help="output folder (default models/piper/)")
    ap.add_argument("--url", default=None,
                    help="download exactly this .onnx (and this URL + '.json') instead of deriving it")
    args = ap.parse_args()

    onnx_url = args.url or derive_url(args.voice)
    out_dir = Path(args.out_dir)
    name = Path(urlparse(onnx_url).path).name
    if not name.endswith(".onnx"):
        print(f"--url must point at a .onnx file, got: {onnx_url}")
        return 1

    for url, dest in ((onnx_url, out_dir / name), (onnx_url + ".json", out_dir / (name + ".json"))):
        try:
            download(url, dest)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"\nFAILED: {url}\n  {exc.__class__.__name__}: {exc}")
            print("If this was a 404 or a timeout, try the URL by hand in a browser:")
            print(f"  {url}")
            print("or browse https://huggingface.co/rhasspy/piper-voices for the right path and "
                  "re-run with --url <the .onnx URL>.")
            return 1

    print(f"\nDone. Set TTS_MODEL_PATH in backend-ai/.env if this is not {settings.tts_model_path} "
          f"(now: {out_dir / name}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
