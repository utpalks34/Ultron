"""Ollama model manager. The GPU has exactly one tenant; CPU models never touch it.

- CPU residents (router, embedder): loaded once, num_gpu=0, keep_alive=-1.
- One GPU worker at a time, under an asyncio lock. It STAYS resident across the hops of a
  run (per-node eviction would reload it on every hop) and is evicted by release(), which
  the orchestrator calls when a run ends (finish, error or cancel).
- Load options here MUST equal the options agents pass (num_ctx, num_gpu), or Ollama
  reloads the model on the next request.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from config import settings, CPU_MODELS, GPU_MODELS, VRAM_COST_MB, is_cpu

log = logging.getLogger("ultron.ollama")

try:
    import pynvml
    pynvml.nvmlInit()
    _HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception as exc:   # no NVML: degrade, do not crash
    log.warning("NVML unavailable (%s) - VRAM telemetry disabled", exc)
    pynvml = None
    _HANDLE = None


class OllamaError(RuntimeError):
    pass


def norm(name: str) -> str:
    """Ollama reports 'nomic-embed-text:latest' for 'nomic-embed-text'."""
    return name if ":" in name else f"{name}:latest"


class OllamaManager:
    def __init__(self, bus):
        self.bus = bus
        self._gpu_tenant: str | None = None
        self._gpu_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(base_url=settings.ollama_host,
                                         timeout=httpx.Timeout(600.0, connect=5.0))
        self.resident_ok = False
        self.startup_error: str | None = None

    @property
    def active(self) -> str | None:
        return self._gpu_tenant

    # -- transport ------------------------------------------------------------
    async def _post(self, path: str, body: dict) -> None:
        try:
            r = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama unreachable at {settings.ollama_host} "
                              f"({exc.__class__.__name__}) - is it running?") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text)
            except Exception:
                detail = r.text
            hint = " - run scripts\\pull_models.py" if r.status_code == 404 else ""
            raise OllamaError(f"{body.get('model')}: {detail}{hint}")

    async def _get(self, path: str) -> dict:
        try:
            r = await self._client.get(path, timeout=5.0)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama {path} failed ({exc.__class__.__name__})") from exc

    async def _warn(self, source: str, message: str) -> None:
        log.warning("%s: %s", source, message)
        await self.bus.publish({"type": "error", "payload": {
            "severity": "warn", "source": source, "message": message}})

    # -- primitives -----------------------------------------------------------
    async def _load(self, model: str, keep_alive) -> None:
        opts = {"num_ctx": settings.ctx_router if is_cpu(model) else settings.ctx_worker}
        if is_cpu(model):
            opts["num_gpu"] = 0
        if model == settings.embed_model:
            # embedding-only models reject /api/generate; a real /api/embed call loads them
            await self._post("/api/embed", {"model": model, "input": "warmup",
                                            "keep_alive": keep_alive, "options": opts})
        else:
            await self._post("/api/generate", {"model": model, "prompt": "",
                                               "keep_alive": keep_alive, "options": opts})

    async def _unload(self, model: str) -> None:
        if model == settings.embed_model:
            await self._post("/api/embed", {"model": model, "input": "x", "keep_alive": 0})
        else:
            await self._post("/api/generate", {"model": model, "prompt": "", "keep_alive": 0})

    def _mem(self) -> tuple[int, int] | None:
        if _HANDLE is None:
            return None
        m = pynvml.nvmlDeviceGetMemoryInfo(_HANDLE)
        return m.used // 1_048_576, m.total // 1_048_576

    def _free_mb(self) -> int | None:
        mem = self._mem()
        return None if mem is None else mem[1] - mem[0]

    async def ps(self) -> list[dict]:
        """Loaded models with their GPU share, from Ollama's /api/ps."""
        out = []
        for m in (await self._get("/api/ps")).get("models", []):
            size, vram = int(m.get("size", 0)), int(m.get("size_vram", 0))
            out.append({"name": m.get("name", "?"), "size_mb": size // 1_048_576,
                        "vram_mb": vram // 1_048_576,
                        "gpu_pct": round(100 * vram / size) if size else 0})
        return out

    async def _check_placement(self, model: str, expect_gpu: bool) -> None:
        try:
            entry = next((m for m in await self.ps() if m["name"] == norm(model)), None)
        except OllamaError:
            return
        if entry is None:
            return
        pct = entry["gpu_pct"]
        if expect_gpu and pct < 95:
            await self._warn("vram", f"{model} is only {pct}% on GPU (partial offload, "
                             "expect slow generation) - close Chromium/other GPU apps or "
                             "raise OLLAMA_GPU_OVERHEAD")
        if not expect_gpu and pct > 0:
            await self._warn("ollama", f"{model} is {pct}% on GPU but must be CPU-only - "
                             "num_gpu=0 is missing on a call path")

    # -- lifecycle ------------------------------------------------------------
    async def warm_resident(self) -> bool:
        """Router and embedder onto CPU, forever. Never raises; sets startup_error."""
        errors: list[str] = []
        for model in sorted(CPU_MODELS):
            try:
                await self._load(model, keep_alive=-1)
            except OllamaError as exc:
                log.warning("resident warm-up failed: %s", exc)
                if str(exc) not in errors:
                    errors.append(str(exc))
                continue
            await self._check_placement(model, expect_gpu=False)
            log.info("resident on CPU: %s", model)
        self.resident_ok = not errors
        self.startup_error = "; ".join(errors) or None
        return self.resident_ok

    async def ensure_resident(self) -> None:
        if not self.resident_ok:
            await self.warm_resident()

    @asynccontextmanager
    async def worker(self, model: str):
        """Exclusive GPU tenancy for one agent node. Does NOT evict on exit: the worker
        stays loaded for the next hop; release() evicts it when the run ends."""
        if model not in GPU_MODELS:
            raise ValueError(f"{model} is not a GPU model - check config")
        async with self._gpu_lock:
            if self._gpu_tenant and self._gpu_tenant != model:
                await self._evict_locked()
            if self._gpu_tenant != model:
                free, need = self._free_mb(), VRAM_COST_MB.get(model, 2200)
                if free is not None and free < need:
                    await self._warn("vram", f"{free} MB free, {model} wants {need} MB - "
                                     "Ollama will offload layers to CPU and run slowly")
                await self.bus.publish({"type": "model",
                                        "payload": {"phase": "loading", "model": model}})
                # set first: a cancel mid-load must still be evicted by release()
                self._gpu_tenant = model
                try:
                    await self._load(model, keep_alive="5m")
                except OllamaError:
                    await self.bus.publish({"type": "model",
                                            "payload": {"phase": "evicted", "model": model}})
                    self._gpu_tenant = None
                    raise
                await self._check_placement(model, expect_gpu=True)
                await self.bus.publish({"type": "model",
                                        "payload": {"phase": "ready", "model": model}})
            yield model

    async def _evict_locked(self) -> None:
        model = self._gpu_tenant
        if model is None:
            return
        await self.bus.publish({"type": "model",
                                "payload": {"phase": "unloading", "model": model}})
        # Keep the tenant set on CancelledError: the model may still be loaded, so the
        # next release() must retry. Clear it after success, or when Ollama itself failed.
        try:
            await self._unload(model)
        except OllamaError:
            self._gpu_tenant = None
            raise
        self._gpu_tenant = None
        await asyncio.sleep(0.3)   # Windows releases VRAM asynchronously
        await self.bus.publish({"type": "model",
                                "payload": {"phase": "evicted", "model": model}})

    async def release(self) -> None:
        """Evict the GPU tenant. The orchestrator calls this when a run ends."""
        async with self._gpu_lock:
            await self._evict_locked()

    # -- telemetry ------------------------------------------------------------
    async def snapshot(self) -> dict:
        mem = self._mem()
        used, total = mem if mem else (0, 0)
        return {"used_mb": used, "total_mb": total, "active_model": self._gpu_tenant}

    async def telemetry(self, hz: float | None = None) -> None:
        if _HANDLE is None:
            return
        interval = 1.0 / (hz or settings.vram_poll_hz)
        while True:
            try:
                await self.bus.publish({"type": "vram", "payload": await self.snapshot()})
            except Exception as exc:   # an NVML hiccup must not kill the gauge
                log.warning("vram poll failed: %s", exc)
            await asyncio.sleep(interval)

    async def shutdown(self) -> None:
        """Evict our models that are loaded, then close the client."""
        names = {norm(m): m for m in (*GPU_MODELS, *CPU_MODELS)}
        try:
            for entry in await self.ps():
                if entry["name"] in names:
                    await self._unload(names[entry["name"]])
        except Exception:
            pass
        self._gpu_tenant = None
        await self._client.aclose()
