"""Worker throughput benchmark: tokens/s and GPU share of the worker model, straight
against Ollama (no backend involved).

The backend must be idle (no worker loaded) while this runs, or the GPU is shared and the
numbers are meaningless. Needs Ollama running with the worker model pulled.

    python scripts\\bench_worker.py [--model qwen2.5-coder:3b] [--num-ctx 4096] [--runs 3]
                                   [--host http://127.0.0.1:11434]

PASS needs >= 30 tokens/s on every run and >= 95% of the model on the GPU.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / "backend-ai" / ".env"
PROMPT = "Explain in detail how a hash map works."
MIN_TPS = 30.0
MIN_GPU_PCT = 95.0
POLL_S = 0.3


def default_host() -> str:
    # same precedence as config.py: real environment variable, then .env, then the default
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host and ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OLLAMA_HOST=") and line.split("=", 1)[1].strip():
                host = line.split("=", 1)[1].strip().strip('"').strip("'")
    host = (host or "http://127.0.0.1:11434").rstrip("/")
    return host if host.startswith(("http://", "https://")) else f"http://{host}"


def norm(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


async def poll_gpu_share(c: httpx.AsyncClient, model: str, stop: asyncio.Event) -> float:
    best = 0.0
    while not stop.is_set():
        try:
            r = await c.get("/api/ps", timeout=5.0)
            for m in r.json().get("models", []):
                size = int(m.get("size", 0))
                if m.get("name") == norm(model) and size:
                    best = max(best, 100.0 * int(m.get("size_vram", 0)) / size)
        except (httpx.HTTPError, ValueError):
            pass
        try:
            await asyncio.wait_for(stop.wait(), POLL_S)
        except asyncio.TimeoutError:
            pass
    return best


async def one_run(c: httpx.AsyncClient, args) -> tuple[dict, float]:
    stop = asyncio.Event()
    poller = asyncio.create_task(poll_gpu_share(c, args.model, stop))
    try:
        r = await c.post("/api/generate", timeout=300.0, json={
            "model": args.model, "prompt": PROMPT, "stream": False, "keep_alive": 0,
            "options": {"num_ctx": args.num_ctx, "num_predict": 200, "temperature": 0}})
        r.raise_for_status()
        data = r.json()
    finally:
        stop.set()
        share = await poller
    return data, share


async def main(args) -> int:
    async with httpx.AsyncClient(base_url=args.host) as c:
        try:
            await c.get("/api/tags", timeout=5.0)
        except httpx.HTTPError as exc:
            print(f"Ollama unreachable at {args.host} ({exc.__class__.__name__}) - is it running?")
            return 1

        print(f"model {args.model}, num_ctx {args.num_ctx}, {args.runs} run(s)\n")
        speeds, shares = [], []
        for i in range(1, args.runs + 1):
            try:
                data, share = await one_run(c, args)
            except httpx.HTTPStatusError as exc:
                print(f"run {i}: Ollama returned {exc.response.status_code}: {exc.response.text[:200]}")
                return 1
            except httpx.HTTPError as exc:
                print(f"run {i}: request failed ({exc.__class__.__name__})")
                return 1
            n, dur = data.get("eval_count", 0), data.get("eval_duration", 0)
            tps = n / (dur / 1e9) if dur else 0.0
            speeds.append(tps)
            shares.append(share)
            print(f"run {i}: {tps:6.1f} tok/s ({n} tokens), "
                  f"load {data.get('load_duration', 0) / 1e9:.2f} s, "
                  f"prompt_eval {data.get('prompt_eval_duration', 0) / 1e9:.2f} s "
                  f"({data.get('prompt_eval_count', 0)} tokens), GPU {share:.0f}%")

    slowest, gpu = min(speeds), min(shares)
    print(f"\nmin {slowest:.1f} tok/s (need >= {MIN_TPS:.0f}), "
          f"GPU share seen {gpu:.0f}% (need >= {MIN_GPU_PCT:.0f}%)")
    ok_speed, ok_gpu = slowest >= MIN_TPS, gpu >= MIN_GPU_PCT
    if not ok_speed:
        print("FAIL: too slow - check nvidia-smi during generation for partial offload")
    if not ok_gpu:
        print("FAIL: model not fully on the GPU - close Chromium/other GPU apps, "
              "raise OLLAMA_GPU_OVERHEAD, and make sure the backend is idle")
    if ok_speed and ok_gpu:
        print("PASS")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="qwen2.5-coder:3b")
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--host", default=default_host())
    sys.exit(asyncio.run(main(ap.parse_args())))
