"""VRAM discipline check (Phase-Wise Plan, Phase 4 step 7).

Runs three tasks back to back through the real backend and asserts, per task:
peak dedicated VRAM under the limit, the worker fully on the GPU while it runs, VRAM back
near baseline within 2 s of the answer, and afterwards `ollama ps` shows no worker, the
router on CPU only (vram 0) and the embedder, if listed, on CPU only.

Needs Ollama running and the backend running with DEBUG_ENDPOINTS=1. The session token is
taken from ULTRON_SESSION_TOKEN, else SESSION_TOKEN in backend-ai/.env (never printed).

    python scripts\\check_vram.py [--host 127.0.0.1] [--port 8765] [--limit-mb 2900]
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / "backend-ai" / ".env"
TASKS = ["what is in my notes about the blueprint",
         "open my editor",
         "what does Gita 2.47 say"]
SAMPLE_S = 0.25
RECOVERY_SLACK_MB = 200
RECOVERY_MAX_S = 5.0
RECOVERY_LIMIT_S = 2.0
MIN_GPU_PCT = 95


def load_env() -> dict:
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def norm(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


class DebugError(RuntimeError):
    pass


async def get_vram(c: httpx.AsyncClient) -> dict:
    r = await c.get("/debug/vram", timeout=10.0)
    if r.status_code == 401:
        raise DebugError("401 from /debug/vram: the session token does not match the backend's")
    if r.status_code == 404:
        raise DebugError("404 from /debug/vram: start the backend with DEBUG_ENDPOINTS=1")
    r.raise_for_status()
    return r.json()


def used_mb(d: dict) -> int:
    return int(d["vram"]["used_mb"])


def find(ps, name: str):
    return next((m for m in (ps or []) if m["name"] == norm(name)), None)


def ps_table(ps) -> str:
    if ps is None:
        return "  (ollama ps unavailable)"
    if not ps:
        return "  (no models loaded)"
    lines = [f"  {'NAME':<28} {'SIZE MB':>8} {'VRAM MB':>8} {'GPU %':>6}"]
    for m in ps:
        lines.append(f"  {m['name']:<28} {m['size_mb']:>8} {m['vram_mb']:>8} {m['gpu_pct']:>6}")
    return "\n".join(lines)


async def run_task(c: httpx.AsyncClient, text: str, worker: str, baseline: int):
    run = asyncio.create_task(
        c.post("/debug/run", json={"text": text, "wait": True}, timeout=180.0))
    peak, seen_pct = 0, []
    last_seen = None                   # time of the last sample in which the worker was in ps
    while not run.done():
        try:
            d = await get_vram(c)
            peak = max(peak, used_mb(d))
            w = find(d.get("ps"), worker)
            if w:
                seen_pct.append(w["gpu_pct"])
                last_seen = time.perf_counter()
        except (httpx.HTTPError, DebugError):
            pass                       # a missed sample is fine; the run result is authoritative
        await asyncio.sleep(SAMPLE_S)
    resp = await run
    if resp.status_code == 409:
        raise DebugError("backend is busy with another run; wait for it to finish")
    resp.raise_for_status()

    # recovery is measured from the last sample that still showed the worker, so it includes
    # the backend's own release() time; if the worker was never seen, from the end of the run
    t0 = time.perf_counter()
    if last_seen is None:
        last_seen = t0
    recovery = None
    while time.perf_counter() - t0 <= RECOVERY_MAX_S:
        d = await get_vram(c)
        t_sample = time.perf_counter()
        if used_mb(d) <= baseline + RECOVERY_SLACK_MB:
            recovery = max(0.0, t_sample - last_seen)
            break
        await asyncio.sleep(SAMPLE_S)

    await asyncio.sleep(1.0)
    d = await get_vram(c)
    return peak, seen_pct, recovery, d


async def main(args) -> int:
    env = load_env()
    # same precedence as config.py: real environment variable, then .env, then the default
    def setting(key: str, default: str = "") -> str:
        return os.environ.get(key) or env.get(key) or default

    token = os.environ.get("ULTRON_SESSION_TOKEN") or setting("SESSION_TOKEN")
    if not token:
        print("No session token: set ULTRON_SESSION_TOKEN or SESSION_TOKEN in backend-ai/.env")
        return 1
    worker = setting("WORKER_MODEL", "qwen2.5-coder:3b")
    router = setting("ROUTER_MODEL", "qwen2.5:1.5b-instruct")
    embedder = setting("EMBED_MODEL", "nomic-embed-text")

    async with httpx.AsyncClient(base_url=f"http://{args.host}:{args.port}",
                                 headers={"X-Ultron-Token": token}) as c:
        try:
            d = await get_vram(c)
        except httpx.HTTPError as exc:
            print(f"Backend unreachable at {args.host}:{args.port} ({exc.__class__.__name__}) - "
                  "is it running with DEBUG_ENDPOINTS=1?")
            return 1
        except DebugError as exc:
            print(exc)
            return 1
        if not d.get("resident_ok"):
            print("Ollama residents not loaded: start Ollama, then restart the backend")
            return 1
        if not d["vram"].get("total_mb"):
            print("NVML reports no GPU memory (telemetry disabled): cannot measure VRAM")
            return 1
        baseline = used_mb(d)
        print(f"baseline {baseline} MB of {d['vram']['total_mb']} MB, limit {args.limit_mb} MB\n")
        print("recovery = time from the last sample showing the worker to VRAM back at baseline "
              "(resolution about 0.25 s)\n")
        print(f"{'task':<44} {'peak MB':>8} {'GPU %':>6} {'recov s':>8}  result")
        print("-" * 78)

        failures, ever_seen, last_ps = [], False, d.get("ps")
        for text in TASKS:
            try:
                peak, seen_pct, recovery, after = await run_task(c, text, worker, baseline)
            except (httpx.HTTPError, DebugError) as exc:
                print(f"{text[:43]:<44} run failed: {exc}")
                failures.append(f"{text!r}: run failed ({exc})")
                continue

            ps = after.get("ps")
            last_ps = ps
            why = []
            if peak >= args.limit_mb:
                why.append(f"peak {peak} MB >= {args.limit_mb} MB")
            if not seen_pct:
                why.append("worker never seen")
            else:
                ever_seen = True
                if min(seen_pct) < MIN_GPU_PCT:
                    why.append(f"worker GPU share {min(seen_pct)}% < {MIN_GPU_PCT}%")
            if recovery is None:
                why.append(f"VRAM did not return to baseline within {RECOVERY_MAX_S:.0f}s")
            elif recovery > RECOVERY_LIMIT_S:
                why.append(f"recovery {recovery:.2f}s > {RECOVERY_LIMIT_S:.1f}s")
            if ps is None:
                why.append(f"ollama ps failed: {after.get('ps_error')}")
            else:
                if find(ps, worker):
                    why.append("worker still listed in ollama ps")
                r = find(ps, router)
                if r is None:
                    why.append("router not resident")
                elif r["vram_mb"] != 0:
                    why.append(f"router uses {r['vram_mb']} MB of VRAM")
                e = find(ps, embedder)
                if e is not None and e["vram_mb"] != 0:
                    why.append(f"embedder uses {e['vram_mb']} MB of VRAM")

            gpu = f"{min(seen_pct)}" if seen_pct else "-"
            rec = f"{recovery:.2f}" if recovery is not None else "-"
            print(f"{text[:43]:<44} {peak:>8} {gpu:>6} {rec:>8}  {'FAIL' if why else 'PASS'}")
            for w in why:
                print(f"    - {w}")
                failures.append(f"{text!r}: {w}")

        print("\nollama ps (after the last task):")
        print(ps_table(last_ps))

        if not ever_seen:
            print("\nWorker never seen: routing sent every task to NONE or the worker never loaded")
        if failures:
            print(f"\nVRAM discipline FAILED ({len(failures)} problem(s))")
            return 1
        print("\nVRAM discipline OK")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--limit-mb", type=int, default=2900)
    sys.exit(asyncio.run(main(ap.parse_args())))
