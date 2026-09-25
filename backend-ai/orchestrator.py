import asyncio
import json
import logging
import os
import secrets
import time
import traceback
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
import psutil
from langchain_core.messages import AIMessage, HumanMessage

from bus import EventBus
from config import settings
from graph.builder import build_graph
from graph.checkpointer import make_checkpointer
from graph.state import WORKER_NAMES, SELF_PUBLISHING
from llm.ollama_manager import OllamaManager, OllamaError
from memory import episodes
from memory.db import init_pool, close_pool
from sensors import tts
from sensors.stt import SttEngine
from sensors.wakeword import WakeWordEngine
from tools import approvals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ultron.orchestrator")

HOST = "127.0.0.1"
PORT = 8765
HEARTBEAT_S = 1.0
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "shared" / "events.schema.json"

bus = EventBus()
ollama = OllamaManager(bus)
current_phase = "idle"
current_run_id: str | None = None
active_node: str | None = None
chat_log: deque = deque(maxlen=50)      # {"role","text"} dicts, feeds the snapshot
RUNS: dict[str, asyncio.Task] = {}
startup_warnings: list[dict] = []   # {"source","message"}, published on every WS connect
run_source: dict[str, str] = {}     # run_id -> "voice" or "text"; only decides whether to speak the answer

# Unknown keywords such as the custom "x-direction" are ignored by the validator; no metaschema check is run.
_validator: Draft202012Validator | None = None
if settings.dev_validate or settings.debug_endpoints:
    _validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _envelope(type_: str, payload: dict) -> dict:
    return {"v": 1, "id": str(uuid.uuid4()), "ts": int(time.time() * 1000), "type": type_, "payload": payload}


def _validate_outbound(evt: dict) -> None:
    if not settings.dev_validate or _validator is None:
        return
    err = next(_validator.iter_errors(evt), None)
    if err is not None:
        log.error("schema violation on outbound %r event: %s", evt.get("type"), err.message)


async def _send(ws: WebSocket, lock: asyncio.Lock, evt: dict) -> None:
    _validate_outbound(evt)
    async with lock:
        await ws.send_text(json.dumps(evt))


async def _heartbeat() -> None:
    while True:
        evt = {"type": "state", "payload": {"phase": current_phase}}
        if current_run_id is not None:
            evt["run_id"] = current_run_id
        await bus.publish(evt)
        await asyncio.sleep(HEARTBEAT_S)


async def set_phase(phase: str, run_id: str | None = None) -> None:
    """Every phase change goes through here so the heartbeat never overwrites it."""
    global current_phase
    current_phase = phase
    evt = {"type": "state", "payload": {"phase": phase}}
    if run_id:
        evt["run_id"] = run_id
    await bus.publish(evt)


async def emit_answer(run_id: str, text: str, streamed: bool = False) -> None:
    """Record the answer in chat_log; publish it unless its tokens were already streamed."""
    chat_log.append({"role": "assistant", "text": text})
    await bus.publish({"type": "token", "run_id": run_id,
                       "payload": {"text": "" if streamed else text,
                                   "role": "assistant", "done": True}})


def _ai_messages(output) -> list:
    if not isinstance(output, dict):
        return []
    return [m for m in (output.get("messages") or []) if isinstance(m, AIMessage)]


async def run_graph(run_id: str, text: str) -> None:
    global active_node, current_run_id
    current_run_id = run_id
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
    state = {"messages": [HumanMessage(content=text)], "run_id": run_id, "hops": 0, "scratch": {}}
    open_runs: dict[str, str] = {}   # node -> LangChain run id of the node's OUTER run
    t0: dict[str, float] = {}
    streamed: set[str] = set()
    visited: list[str] = []
    answers: list[str] = []
    outcome = "ok"

    async def close_nodes(status: str) -> None:
        global active_node
        for n in list(open_runs):
            await bus.publish({"type": "graph_node", "run_id": run_id,
                               "payload": {"node": n, "status": status}})
        for _ in list(streamed):
            await bus.publish({"type": "token", "run_id": run_id,
                               "payload": {"text": "", "role": "assistant", "done": True}})
        streamed.clear()
        open_runs.clear()
        active_node = None

    try:
        await set_phase("thinking", run_id)
        await ollama.ensure_resident()
        async for ev in app.state.graph.astream_events(state, cfg, version="v2"):
            kind, name = ev["event"], ev.get("name", "")
            if kind == "on_chain_start" and name in settings.graph_nodes and name not in open_runs:
                open_runs[name] = ev["run_id"]
                t0[name] = time.perf_counter()
                active_node = name
                await bus.publish({"type": "graph_node", "run_id": run_id,
                                   "payload": {"node": name, "status": "active"}})
                if name != "supervisor":
                    visited.append(name)
                # Phase 3: node-based phases. Phase 4+ tool events refine "acting".
                await set_phase("thinking" if name == "supervisor" else "acting", run_id)
            elif kind == "on_chain_end" and name in open_runs and open_runs[name] == ev["run_id"]:
                ms = int((time.perf_counter() - t0.pop(name)) * 1000)
                del open_runs[name]
                msgs = _ai_messages(ev["data"].get("output"))
                was_streamed = name in streamed
                streamed.discard(name)
                for m in msgs:
                    pub = was_streamed or bool((getattr(m, "additional_kwargs", None) or {}).get("published"))
                    await emit_answer(run_id, str(m.content), streamed=pub)
                    answers.append(str(m.content))
                if was_streamed and not msgs:
                    await bus.publish({"type": "token", "run_id": run_id,
                                       "payload": {"text": "", "role": "assistant", "done": True}})
                await bus.publish({"type": "graph_node", "run_id": run_id,
                                   "payload": {"node": name, "status": "done", "ms": ms}})
                if active_node == name:
                    active_node = None
            elif kind == "on_chat_model_stream":
                md = ev.get("metadata", {})
                node = (md.get("langgraph_checkpoint_ns") or md.get("langgraph_node") or "").split(":")[0]
                if node in WORKER_NAMES and node not in SELF_PUBLISHING:   # never stream router JSON into the chat
                    chunk = ev["data"]["chunk"].content
                    if chunk:
                        streamed.add(node)
                        await bus.publish({"type": "token", "run_id": run_id,
                                           "payload": {"text": chunk, "role": "assistant", "done": False}})
            elif kind in ("on_tool_start", "on_tool_end"):
                payload = {"tool": name, "status": "start" if kind == "on_tool_start" else "end"}
                if active_node:
                    payload["agent"] = active_node
                if kind == "on_tool_start":
                    payload["args_preview"] = str(ev["data"].get("input"))[:160]
                await bus.publish({"type": "tool_call", "run_id": run_id, "payload": payload})
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        outcome = "failed"
        log.exception("graph run failed")
        await bus.publish({"type": "error", "run_id": run_id,
                           "payload": {"severity": "error", "source": "graph",
                                       "message": str(exc) or exc.__class__.__name__,
                                       "traceback": traceback.format_exc()[-2000:]}})
        await close_nodes("failed")
        await set_phase("error", run_id)
        await asyncio.sleep(2.0)                   # hold the error state so it is visible
    finally:
        # a task that dies before reaching the end of finally must not leave its run_source entry behind
        source = run_source.pop(run_id, "text")
        # Phase 5 records episode outcomes here.
        approvals.deny_all()                    # no confirmation may outlive its run
        await close_nodes("done")              # no-op unless cancelled mid-node
        try:
            await ollama.release()
        except Exception as exc:
            log.warning("gpu release failed: %s", exc)
            await bus.publish({"type": "error", "run_id": run_id, "payload": {
                "severity": "error", "source": "ollama",
                "message": f"failed to evict the worker: {exc}"}})
        pool = app.state.pool
        if pool is not None:
            try:
                await episodes.log_episode(
                    pool, run_id, visited[-1] if visited else "supervisor",
                    f"[{' > '.join(visited) or 'none'}] {text[:160]} => {' | '.join(answers)[:400]}",
                    outcome)
            except Exception as exc:
                log.warning("episode log failed: %s", exc)
        RUNS.pop(run_id, None)
        current_run_id = None
        if not RUNS:
            await set_phase("idle", run_id)
        answer_text = ". ".join(a for a in answers if a).strip()
        if source == "voice" and outcome != "cancelled" and answer_text:
            log.info("voice answer ready: source=%s outcome=%s answer_len=%d text=%r",
                     source, outcome, len(answer_text), answer_text[:80])
            # speak() is a detached task on purpose: it must not block RUNS from becoming
            # empty, so a new voice or text turn can start (and barge in on this speech)
            # immediately.
            asyncio.create_task(tts.speak(bus, set_phase, run_id, answer_text))
        else:
            log.info("voice answer skipped: source=%s outcome=%s answer_text_empty=%s",
                     source, outcome, not bool(answer_text))


def start_run(text: str) -> str | None:
    """Returns the run id, or None when a run is already active (caller reports 'busy')."""
    # a typed message must interrupt any speech still playing from a previous voice answer, even
    # though this new run itself may never call speak() (only voice-sourced runs do)
    asyncio.create_task(tts.barge_in())
    if RUNS:
        return None
    run_id = secrets.token_hex(8)
    chat_log.append({"role": "user", "text": text})
    task = asyncio.create_task(run_graph(run_id, text))
    RUNS[run_id] = task
    # A task cancelled before its first step never enters run_graph, so its finally never
    # runs and RUNS would stay non-empty ("busy" forever).
    task.add_done_callback(lambda _t, rid=run_id: RUNS.pop(rid, None))
    return run_id


class _PhaseTrackingBus:
    """What SttEngine publishes through. The engine emits `state` events straight to the bus, but
    the heartbeat republishes current_phase every second and would overwrite "listening" with
    "idle" within a second; this keeps current_phase in step with what the engine publishes."""

    async def publish(self, evt: dict) -> None:
        global current_phase
        if evt.get("type") == "state":
            current_phase = evt["payload"]["phase"]
        await bus.publish(evt)


async def handle_voice_transcript(text: str) -> None:
    print(f"!!! DEBUG: handle_voice_transcript called with text={text!r}", flush=True)
    text = text.strip()
    if not text:
        await set_phase("idle")
        return
    rid = start_run(text)
    if rid is None:
        await bus.publish({"type": "error", "payload": {"severity": "warn",
            "source": "orchestrator", "message": "busy: a run is already active"}})
        return
    run_source[rid] = "voice"


async def _pump(ws: WebSocket, lock: asyncio.Lock, q: asyncio.Queue) -> None:
    try:
        while True:
            evt = await q.get()
            await _send(ws, lock, evt)
    except (WebSocketDisconnect, RuntimeError):
        pass  # client went away; the receive loop's cleanup handles the rest


def claim_pid_file() -> None:
    """Warns when the pid file names a live python process (never kills or refuses to start:
    a human decides), then writes this process's pid."""
    path = Path(settings.pid_file)
    try:
        if path.exists():
            try:
                old = int(path.read_text().strip())
                proc = psutil.Process(old)
                if old != os.getpid() and proc.is_running() and "python" in proc.name().lower():
                    log.warning("a previous Ultron backend (pid %d) appears to still be running - "
                                "close it first, or delete %s if this is stale", old, path)
            except (ValueError, psutil.Error):
                pass                          # unreadable or dead pid: stale, overwrite it
        path.write_text(str(os.getpid()))
    except OSError as exc:
        log.warning("pid file %s could not be written: %s", path, exc)


def release_pid_file() -> None:
    """Deletes the pid file only if it still holds this process's own pid."""
    path = Path(settings.pid_file)
    try:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError as exc:
        log.warning("pid file %s could not be removed: %s", path, exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    claim_pid_file()
    log.info("Ultron backend starting on port %d", PORT)
    try:
        _app.state.pool = await init_pool(settings.pg_dsn)
    except Exception as exc:
        _app.state.pool = None
        startup_warnings.append({"source": "database",
            "message": f"PostgreSQL unavailable ({exc.__class__.__name__}: {str(exc)[:160]}) - "
                       "RAG and episode logging are disabled"})
    try:
        saver, closer = await make_checkpointer(settings.checkpointer, settings.pg_dsn)
    except Exception as exc:
        saver, closer = await make_checkpointer("memory", settings.pg_dsn)
        startup_warnings.append({"source": "checkpointer",
            "message": f"{settings.checkpointer} checkpointer failed ({exc.__class__.__name__}: "
                       f"{str(exc)[:160]}); using in-memory"})
    _app.state.stt = None
    _app.state.wake = None
    if settings.voice_enabled:
        stt = SttEngine(_PhaseTrackingBus(), on_final=handle_voice_transcript)
        await asyncio.to_thread(stt.load)
        if stt.ready:
            stt.start(asyncio.get_running_loop())
            print("!!! DEBUG: stt.start() call completed, stt.ready =", stt.ready, flush=True)
            if settings.wake_word_enabled:
                wake = WakeWordEngine(stt)
                await asyncio.to_thread(wake.load)
                if wake.ready:
                    wake.start()
                else:
                    startup_warnings.append({"source": "wakeword", "message":
                        f"wake word unavailable ({wake.load_error})"})
                _app.state.wake = wake
        else:
            startup_warnings.append({"source": "stt",
                "message": f"speech input unavailable ({stt.load_error})"})
        _app.state.stt = stt
        tts_ok = await asyncio.to_thread(tts.load)
        if not tts_ok:
            startup_warnings.append({"source": "tts",
                "message": f"speech output unavailable ({tts.error()})"})
    _app.state.graph = build_graph(bus, ollama, _app.state.pool, checkpointer=saver)
    await ollama.warm_resident()     # before serving, so the first utterance is not a cold start
    task = asyncio.create_task(_heartbeat())
    telemetry_task = asyncio.create_task(ollama.telemetry())
    try:
        yield
    finally:
        for t in (telemetry_task, task):
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t
        runs = list(RUNS.values())
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)
        if _app.state.stt is not None:
            _app.state.stt.cancel()      # best-effort; the daemon thread is not joined
        if _app.state.wake is not None:
            _app.state.wake.stop()       # best-effort; the daemon thread is not joined
        try:
            from tools import browser    # lazy: playwright is not needed until a browser tool runs
            await browser.shutdown()
        except Exception as exc:
            log.warning("browser shutdown failed: %s", exc)
        await ollama.shutdown()
        await close_pool(_app.state.pool)
        if closer:
            await closer()
        release_pid_file()


app = FastAPI(lifespan=lifespan)

CLIENT_TO_SERVER = {"user_utterance", "confirm_response", "cancel", "mic", "ping", "resync"}

if settings.debug_endpoints:

    def _debug_token_ok(request: Request) -> bool:
        supplied = request.headers.get("X-Ultron-Token", "")
        return secrets.compare_digest(supplied.encode("utf-8"), settings.session_token.encode("utf-8"))

    @app.post("/debug/publish")
    async def debug_publish(request: Request) -> JSONResponse:
        if not _debug_token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        type_ = body.get("type")
        payload = body.get("payload")
        run_id = body.get("run_id")
        if not isinstance(type_, str) or not isinstance(payload, dict):
            return JSONResponse({"error": "type must be a string and payload an object"}, status_code=400)
        if run_id is not None and not isinstance(run_id, str):
            return JSONResponse({"error": "run_id must be a string"}, status_code=400)
        if type_ in CLIENT_TO_SERVER:
            return JSONResponse({"error": "client-to-server type"}, status_code=400)

        envelope = {"v": 1, "type": type_, "payload": payload}
        if run_id is not None:
            envelope["run_id"] = run_id

        errors = sorted(_validator.iter_errors(envelope), key=lambda e: list(e.path))
        if errors:
            return JSONResponse(
                {"error": "schema", "details": [e.message for e in errors[:3]]}, status_code=422
            )

        log.log(logging.DEBUG if type_ == "audio_level" else logging.INFO, "debug publish: %s", type_)
        if type_ == "state":
            await set_phase(payload["phase"], run_id)
        else:
            await bus.publish(envelope)
        return JSONResponse({"ok": True})

    @app.post("/debug/run")
    async def debug_run(request: Request) -> JSONResponse:
        if not _debug_token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        text = body.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return JSONResponse({"error": "text must be a non-empty string"}, status_code=400)

        rid = start_run(text)
        if rid is None:
            return JSONResponse({"error": "busy"}, status_code=409)
        task = RUNS[rid]
        log.info("debug run started")
        if body.get("wait") is True:
            await asyncio.wait({task}, timeout=120)
        return JSONResponse({"run_id": rid, "status": "finished" if task.done() else "running"})

    @app.get("/debug/vram")
    async def debug_vram(request: Request) -> JSONResponse:
        if not _debug_token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        result = {"vram": await ollama.snapshot(), "tenant": ollama.active,
                  "resident_ok": ollama.resident_ok}
        try:
            result["ps"] = await ollama.ps()
        except OllamaError as exc:
            result["ps"] = None
            result["ps_error"] = str(exc)
        return JSONResponse(result)

    @app.post("/debug/speak")
    async def debug_speak(request: Request) -> JSONResponse:
        if not _debug_token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        text = body.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return JSONResponse({"error": "text must be a non-empty string"}, status_code=400)

        asyncio.create_task(tts.speak(bus, set_phase, None, text))
        result = {"ok": True, "ready": tts.ready()}
        if not tts.ready():
            result["message"] = f"speech output is not available: {tts.error()}"
        return JSONResponse(result)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = "") -> None:
    if not secrets.compare_digest(token.encode("utf-8"), settings.session_token.encode("utf-8")):
        await ws.close(code=1008)
        return

    await ws.accept()
    q = bus.subscribe()
    lock = asyncio.Lock()
    pump = asyncio.create_task(_pump(ws, lock, q))
    log.info("client connected")
    if ollama.startup_error:
        await bus.publish({"type": "error", "payload": {
            "severity": "warn", "source": "ollama", "message": ollama.startup_error}})
    for w in startup_warnings:
        await bus.publish({"type": "error", "payload": {
            "severity": "warn", "source": w["source"], "message": w["message"]}})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ignoring invalid JSON frame")
                continue
            if not isinstance(msg, dict):
                log.warning("ignoring non-object frame")
                continue

            kind = msg.get("type")
            payload = msg.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            if kind == "ping":
                await _send(ws, lock, _envelope("pong", {}))
            elif kind == "resync":
                snap = await ollama.snapshot()
                await _send(
                    ws,
                    lock,
                    _envelope(
                        "snapshot",
                        {
                            "phase": current_phase,
                            "active_node": active_node,
                            "messages": list(chat_log),
                            "vram": {k: v for k, v in snap.items() if k in ("used_mb", "total_mb")},
                            "active_model": ollama.active,
                        },
                    ),
                )
                for evt in approvals.pending_events():
                    await _send(ws, lock, evt)
            elif kind == "user_utterance":
                text = str(payload.get("text", "")).strip()
                if text:
                    # Never take the run id from the client: it would be reused as the LangGraph
                    # thread_id and could resume an old checkpoint.
                    rid = start_run(text)
                    if rid is None:
                        await bus.publish(
                            {
                                "type": "error",
                                "payload": {
                                    "severity": "warn",
                                    "source": "orchestrator",
                                    "message": "busy: a run is already active - cancel it or wait",
                                },
                            }
                        )
            elif kind == "cancel":
                run_id = payload.get("run_id", "")
                task = RUNS.get(run_id) if isinstance(run_id, str) else None
                # a second cancel arriving inside run_graph's finally would abort release() and the idle transition
                if task and not task.cancelling():
                    # no deny_all() here: it would resolve a pending confirmation to False instead of
                    # letting it raise CancelledError, so a cancel during a confirmation was silently
                    # swallowed and the run continued. run_graph's finally still denies leftovers.
                    task.cancel()
                # unconditional: speech may still be playing after the run that produced it has finished
                asyncio.create_task(tts.barge_in())
            elif kind == "confirm_response":
                action_id, approved = payload.get("action_id"), payload.get("approved")
                if isinstance(action_id, str) and isinstance(approved, bool):
                    log.info("confirm_response approved=%s", approved)
                    await approvals.resolve(action_id, approved)
                else:
                    log.warning("ignoring malformed confirm_response")
            elif kind == "mic":
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    log.warning("ignoring malformed mic message")
                    continue
                log.info("mic enabled=%s", enabled)
                if enabled:
                    await tts.barge_in()     # must finish before arming, or listening starts over audible speech
                    if app.state.stt is None or not app.state.stt.ready:
                        await bus.publish({"type": "error", "payload": {"severity": "warn",
                            "source": "stt", "message": "speech input is not available - "
                            "see the startup warning"}})
                    elif not app.state.stt.arm():
                        await bus.publish({"type": "error", "payload": {"severity": "warn",
                            "source": "stt", "message": "already listening or unavailable"}})
                else:
                    if app.state.stt is not None:
                        app.state.stt.cancel()
                    await tts.barge_in()
            else:
                log.warning("ignoring unknown inbound type %r", kind)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with suppress(asyncio.CancelledError):
            await pump
        bus.unsubscribe(q)
        # without this, a pending confirmation only times out after approval_timeout_s even though
        # nobody is left to answer it; a quick reconnect keeps another subscriber, so it is not denied
        if not bus._subs:
            approvals.deny_all()
        log.info("client disconnected")


if __name__ == "__main__":
    import sys

    import uvicorn

    # psycopg async requires the Selector loop; loop="none" makes uvicorn use the asyncio policy
    # we set; only `python orchestrator.py` is supported for this reason.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # access_log=False: the access log would print "WebSocket /ws?token=<SESSION_TOKEN>" to the console.
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False, loop="none")
