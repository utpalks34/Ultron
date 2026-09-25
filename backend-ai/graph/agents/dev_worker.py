"""Dev worker: diagnoses a terminal error. The error text comes from, in this order, (a) text
pasted in the utterance, (b) the clipboard, (c) the VS Code terminal panel read with OCR. Only
then does it take the GPU, to explain the text. Terminal text is data: it is wrapped in
<terminal_output> and never followed as instructions. It publishes its OWN tokens and marks its
AIMessage published=True, like the RAG and web workers."""
import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from graph.agents.publish import TokenPublisher
from tools import desktop, registry, vision
from tools import resolve as resolver
from tools.context import ToolContext
from tools.desktop import DesktopError

AGENT = "dev_agent"
PROMPTS = Path(__file__).resolve().parents[2] / "llm" / "prompts"
VSCODE_TITLE = "Visual Studio Code"
VSCODE_PROCESS = "Code.exe"
LAUNCH_WAIT_S = 6.0
MIN_OCR_CHARS = 20
HUD_NOTE = ("The window capture came back blank, so I used a screen grab; the Ultron HUD may "
            "overlap the terminal.")

_CLIPBOARD = re.compile(r"\b(?:clipboard|copied)\b", re.I)
_OPEN_VSCODE = re.compile(
    r"\b(?:open|launch|start)\b[^.?!\n]*?\b(?:vs\s?code|vscode|visual\s+studio(?:\s+code)?)\b",
    re.I)


# ---------------------------------------------------------------- pure

def extract_error_text(task: str) -> str | None:
    """PURE: text pasted into the utterance (2+ lines, or an obvious error marker), else None."""
    text = task.strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2 or any(k in text for k in ("Traceback", "Error:", "Exception")):
        return text
    return None


# ---------------------------------------------------------------- sources

@asynccontextmanager
async def _tool(ctx, tool: str, preview: str = ""):
    await ctx.emit(tool, "start", preview)
    try:
        yield
    finally:
        await ctx.emit(tool, "end", preview)


async def _find_vscode() -> dict | None:
    return await asyncio.to_thread(vision.find_window, VSCODE_TITLE, VSCODE_PROCESS)


async def _launch_vscode(ctx, pool, say) -> bool:
    """Open VS Code from the registry and wait for its window; False after saying why not."""
    if pool is None:
        await say("The database is unavailable, so I can't look up VS Code.")
        return False
    if not await registry.gate(ctx, AGENT, "open_app", "Open VS Code"):
        await say("Not approved, so I did not open VS Code.")
        return False
    decision, rows = await resolver.resolve(pool, "app", "vscode")
    if decision != "one":
        await say("I don't have a single VS Code entry in my registry. Add or fix a row in "
                  "registry_apps (name, aliases, launch_cmd) and ask again.")
        return False
    async with _tool(ctx, "open_app", rows[0]["name"]):
        try:
            await desktop.launch(rows[0]["launch_cmd"])
        except DesktopError as exc:
            await say(f"Couldn't open VS Code: {exc}")
            return False
        deadline = time.monotonic() + LAUNCH_WAIT_S
        while time.monotonic() < deadline:
            if await _find_vscode():
                return True
            await asyncio.sleep(0.5)
    return True    # the caller looks once more and answers if there is still no window


async def _screen_text(ctx, bus, pool, run_id, task, say) -> str | None:
    """OCR text of the VS Code terminal panel; None after saying why there is none."""
    win = await _find_vscode()
    if win is None and _OPEN_VSCODE.search(task):
        if not await _launch_vscode(ctx, pool, say):
            return None
        win = await _find_vscode()
    if win is None:
        await say("VS Code isn't open.")
        return None

    # gate first: a denied capture must not still restore and focus the window
    if not await registry.gate(ctx, AGENT, "capture_terminal", "Capture the VS Code terminal"):
        await say("Not approved, so I did not capture the terminal.")
        return None

    if not await asyncio.to_thread(vision.bring_to_front, win["hwnd"]):
        await say("I couldn't bring VS Code to the front, so I didn't read the terminal.")
        return None
    win = await _find_vscode() or win        # the rectangle can change after a restore

    async with _tool(ctx, "capture_terminal", win["title"]):
        img = await asyncio.to_thread(vision.capture_window, win["hwnd"])
        if img is None:
            img = await asyncio.to_thread(vision.grab_region, win["rect"])
            await say(HUD_NOTE + "\n\n")
        crop = vision.terminal_crop(img)
        evt = {"type": "screenshot", "payload": {
            "png_b64": await asyncio.to_thread(vision.to_png_b64, crop),
            "caption": "terminal (VS Code)"}}
        if run_id:
            evt["run_id"] = run_id
        await bus.publish(evt)
        text = await asyncio.to_thread(vision.ocr, crop)
    text = vision.tail_text(text, settings.ocr_max_chars)
    if len(text.strip()) < MIN_OCR_CHARS:
        await say("I couldn't read anything in the terminal panel - is it open?")
        return None
    return text


async def _terminal_text(ctx, bus, pool, run_id, task, say) -> tuple[str, str] | None:
    """(terminal text, task line for the model), or None after saying why there is none."""
    pasted = extract_error_text(task)
    if pasted is not None:
        return vision.tail_text(pasted, settings.ocr_max_chars), "Explain the error above."
    if _CLIPBOARD.search(task):
        if not await registry.gate(ctx, AGENT, "read_clipboard", "Read the clipboard"):
            await say("Not approved, so I did not read the clipboard.")
            return None
        async with _tool(ctx, "read_clipboard"):
            text = await vision.read_clipboard()
        if not text.strip():
            await say("The clipboard is empty.")
            return None
        return text, task
    text = await _screen_text(ctx, bus, pool, run_id, task, say)
    return None if text is None else (text, task)


# ---------------------------------------------------------------- worker

def _wrap(text: str) -> str:
    text = text.replace("<terminal_output>", "").replace("</terminal_output>", "")
    return f"<terminal_output>\n{text}\n</terminal_output>"


def _last_human(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def dev_run_worker(name, state, config, bus, ollama, *, pool) -> AIMessage:
    run_id = state.get("run_id")
    task = (state.get("scratch") or {}).get("task") or _last_human(state)
    ctx = ToolContext(bus, pool, run_id, AGENT)
    pub = TokenPublisher(bus, run_id)
    parts: list[str] = []

    async def say(text: str) -> None:
        if text:
            parts.append(text)
            await pub.token(text)

    cm = asyncio.timeout(settings.worker_timeout_s)
    try:
        async with cm:
            got = await _terminal_text(ctx, bus, pool, run_id, task, say)
            if got is not None:
                text, task_line = got
                system = (PROMPTS / "dev_agent.md").read_text(encoding="utf-8")
                user = f"{_wrap(text)}\n\nTask: {task_line}"
                async with ollama.worker(settings.worker_model) as model:
                    # same options as worker.py so Ollama does not reload the model; no config=
                    # because this node streams its own tokens through the publisher
                    llm = ChatOllama(model=model, base_url=settings.ollama_host,
                                     temperature=0.1, num_ctx=settings.ctx_worker,
                                     num_predict=settings.worker_max_tokens, keep_alive="5m")
                    async for chunk in llm.astream([SystemMessage(content=system),
                                                    HumanMessage(content=user)]):
                        await say(str(chunk.content))
    except TimeoutError:
        await pub.close()
        if cm.expired():
            raise RuntimeError(f"dev_agent: no answer within {settings.worker_timeout_s:.0f}s") \
                from None
        raise
    except BaseException:
        await pub.close()   # close the chat bubble even when the run fails or is cancelled
        raise
    if not parts:
        await say("(no output)")     # published, so the joined chunks equal the returned content
    # no .strip(): the returned text must equal the published chunks exactly
    return AIMessage(content="".join(parts), name=name, additional_kwargs={"published": True})
