"""Lists the audio devices and, with --meter, shows a live microphone level so you can pick
stt_input_device and stt_rms_threshold. Run from backend-ai/ with the venv active:

    python ..\\scripts\\list_audio_devices.py
    python ..\\scripts\\list_audio_devices.py --meter            # 5 seconds, default input
    python ..\\scripts\\list_audio_devices.py --meter 10 --device 3
    python ..\\scripts\\list_audio_devices.py --meter 10 --device "Headset Microphone"

The default input/output change when you plug in headphones, so re-check before first use.
--device takes an index or a name substring and only applies to this run; it does not write to
.env. STT_INPUT_DEVICE in .env is a NAME SUBSTRING, not an index.
"""
import argparse
import sys
import time
from pathlib import Path

import sounddevice as sd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend-ai"))

from config import settings  # noqa: E402
from sensors.audio_device import resolve_input_device  # noqa: E402
from sensors.stt import rms  # noqa: E402

BAR_WIDTH = 50
BAR_FULL_SCALE = 0.25   # an RMS of this value or more fills the bar


def default_index(kind: str) -> int | None:
    try:
        return int(sd.query_devices(kind=kind)["index"])
    except Exception:
        return None


def list_devices() -> None:
    devices = sd.query_devices()
    default_in, default_out = default_index("input"), default_index("output")
    print(f"{'idx':>3}  {'':<8} {'name':<44} {'in':>3} {'out':>3} {'rate':>7}")
    for i, d in enumerate(devices):
        tags = []
        if i == default_in:
            tags.append("IN*")
        if i == default_out:
            tags.append("OUT*")
        row = (f"{i:>3}  {' '.join(tags):<8} {d['name'][:44]:<44} "
               f"{d['max_input_channels']:>3} {d['max_output_channels']:>3} "
               f"{d['default_samplerate']:>7.0f}")
        # highlight the defaults in bold when the terminal supports it
        print(f"\033[1m{row}\033[0m" if tags and sys.stdout.isatty() else row)
    print("\nIN* / OUT* = current default input / output device.")
    configured = settings.stt_input_device
    resolved = resolve_input_device(configured, settings.stt_input_device_exclude)
    print(f"STT is configured for input device: "
          f"{configured if configured else 'default'}"
          f"{f' (resolves to index {resolved})' if resolved is not None else ''}"
          f" at {settings.stt_sample_rate} Hz.")
    if configured and resolved is None:
        print(f"  No input device matches {configured!r}; the system default is used.")


def parse_device(arg: str | None) -> tuple[int | None, bool]:
    """Returns (index, ok). An int string is an index; anything else is a name substring."""
    if arg is None:
        return resolve_input_device(settings.stt_input_device,
                                    settings.stt_input_device_exclude), True
    try:
        return int(arg), True
    except ValueError:
        idx = resolve_input_device(arg, settings.stt_input_device_exclude)
        return idx, idx is not None


def meter(seconds: float, device_arg: str | None) -> int:
    device, ok = parse_device(device_arg)
    if not ok:
        print(f"no input device matches {device_arg!r}")
        return 1
    try:
        info = sd.query_devices(device, kind="input")
    except Exception as exc:
        print(f"cannot use input device {device!r}: {exc}")
        return 1
    block = max(1, int(settings.stt_sample_rate * settings.stt_block_ms / 1000))
    thr_pos = min(BAR_WIDTH - 1, int(settings.stt_rms_threshold / BAR_FULL_SCALE * BAR_WIDTH))
    print(f"\nMeter on: {info['name']}  (device {device if device is not None else 'default'})")
    print(f"Speak normally, then stay quiet. '|' marks stt_rms_threshold = "
          f"{settings.stt_rms_threshold}. {seconds:g} s.\n")
    peak = 0.0
    quiet_floor = None
    end = time.monotonic() + seconds
    try:
        with sd.InputStream(samplerate=settings.stt_sample_rate, channels=1, dtype="float32",
                            blocksize=block, device=device) as stream:
            while time.monotonic() < end:
                data, _ = stream.read(block)
                level = rms(data[:, 0])
                peak = max(peak, level)
                quiet_floor = level if quiet_floor is None else min(quiet_floor, level)
                filled = min(BAR_WIDTH, int(level / BAR_FULL_SCALE * BAR_WIDTH))
                bar = ["#" if i < filled else "." for i in range(BAR_WIDTH)]
                bar[thr_pos] = "|"
                print(f"\r{''.join(bar)} {level:6.4f}", end="", flush=True)
    except Exception as exc:
        print(f"\nmeter failed: {exc}")
        return 1
    print(f"\n\npeak {peak:.4f}   quietest block {quiet_floor or 0.0:.4f}   "
          f"threshold {settings.stt_rms_threshold}")
    print("Pick a threshold above the quietest level (room noise) and well below the peak "
          "(your speech), then set STT_RMS_THRESHOLD in backend-ai/.env.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meter", nargs="?", type=float, const=5.0, default=None, metavar="SECONDS",
                    help="show a live RMS bar for SECONDS (default 5)")
    ap.add_argument("--device", type=str, default=None,
                    help="input device index or name substring for --meter, this run only")
    args = ap.parse_args()

    list_devices()
    if args.meter is not None:
        return meter(args.meter, args.device)
    if args.device is not None:
        print("(--device is only used together with --meter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
