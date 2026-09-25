"""Supervisor: the CPU router picks the worker for one task segment; classify() is the
keyword fallback and the routing-test oracle."""
import asyncio
import re

from langchain_core.messages import AIMessage, HumanMessage

from config import settings
from graph.state import UltronState

MAX_HOPS = 8

_DEV = re.compile(r"\b(terminal|traceback|stack\s?trace|errors?|erroring|exceptions?|debug(ging)?|bugs?|vs\s?code|vscode|visual studio)\b", re.I)
_RAG = re.compile(r"\b(notes?|gita|verse|chapter|remember|my documents?|what did i|knowledge base)\b", re.I)
_WEB = re.compile(r"\b(browser|browse|search|google|flipkart|amazon|website|webpage|youtube|online)\b|https?://|\.com\b", re.I)
_OS = re.compile(r"\b(open|close|launch|start|quit|kill|play|pause|skip|music|song|volume|apps?|whatsapp|message|files?|folders?)\b", re.I)
_SPLIT = re.compile(r"\s+(?:and\s+)?then\s+", re.I)
YT_MUSIC = re.compile(
    r"\b(?:open|launch|go\s+to|bring\s+up)\b[^.?!\n]*?\byoutube music\b|music\.youtube\.com", re.I)
PLAY = re.compile(r"^\s*(?:please\s+)?(?:play|put on)\b", re.I)


def classify(text: str) -> str | None:
    """Priority order matters: dev, rag, web, then os."""
    for name, rx in (("dev_agent", _DEV), ("rag_agent", _RAG), ("web_agent", _WEB), ("os_agent", _OS)):
        if rx.search(text):
            return name
    return None


_RULES = (("dev_agent", _DEV), ("rag_agent", _RAG), ("web_agent", _WEB), ("os_agent", _OS))


def keyword_hits(text: str) -> list[str]:
    """Workers whose keywords match, in priority order (dev, rag, web, os)."""
    return [name for name, rx in _RULES if rx.search(text)]


async def decide(segment: str, route_fn) -> tuple[str | None, str]:
    """Hybrid routing. Returns (worker or None, source). May raise if route_fn raises.
    - two deterministic overrides first ("play ...", then opening YouTube Music), no model call
    - exactly one keyword hit: use it, no model call
    - two or more hits: the LLM chooses; if it answers outside the hits (or NONE), use
      the first hit in priority order
    - no hits: the LLM decides alone (paraphrases and chit-chat)"""
    # "play X on youtube" would otherwise hit both the web and the os keywords and the 1.5B
    # model may pick the browser; playing is the OS agent's job, opening YouTube Music is the
    # web agent's. PLAY is checked first: "play X on youtube music" must go to os_agent
    # (parse_play already handles it as an online source); YT_MUSIC only catches phrasing that
    # clearly means "open the site".
    if PLAY.search(segment):
        return "os_agent", "override"
    if YT_MUSIC.search(segment):
        return "web_agent", "override"
    hits = keyword_hits(segment)
    if len(hits) == 1:
        return hits[0], "keyword"
    route, _reason, secs = await route_fn(segment)
    if hits:
        if route in hits:
            return route, f"llm {secs:.1f}s"
        return hits[0], "keyword priority"
    return route, f"llm {secs:.1f}s"


def split_segments(text: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


def _evt(type_: str, payload: dict, run_id: str | None) -> dict:
    evt = {"type": type_, "payload": payload}
    if run_id:
        evt["run_id"] = run_id
    return evt


def make_supervisor(bus, route_fn=None):
    if route_fn is None:
        from llm.router import route_segment
        route_fn = route_segment

    async def supervisor(state: UltronState) -> dict:
        hops = state.get("hops", 0)
        run_id = state.get("run_id")
        if hops >= MAX_HOPS:
            return {"next": "FINISH", "messages": [AIMessage(
                content=f"Stopped: hop limit ({MAX_HOPS}) reached.", name="supervisor")]}

        messages = state["messages"]
        human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        text = str(human.content) if human else ""

        # debug-only fail-visible test hook
        if settings.debug_endpoints and text.strip().lower().startswith("!crash"):
            raise RuntimeError("crash test (!crash)")

        segments = split_segments(text)
        answered = sum(1 for m in messages if isinstance(m, AIMessage))
        if answered >= len(segments):
            return {"next": "FINISH", "hops": hops + 1}

        segment = segments[answered]
        try:
            route, source = await asyncio.wait_for(
                decide(segment, route_fn), timeout=settings.router_timeout_s)
        except Exception as exc:
            route, source = classify(segment), "keyword fallback"
            await bus.publish(_evt("error", {
                "severity": "warn", "source": "router",
                "message": f"router failed ({exc.__class__.__name__}: {str(exc)[:160]}); "
                           "using keyword fallback"}, run_id))

        if route is None:
            from llm.chat import chat_reply_stream   # lazy: matches the router's own lazy import
            parts = []

            async def _stream():
                async for piece in chat_reply_stream(segment):
                    parts.append(piece)
                    evt = {"type": "token", "payload": {"text": piece, "role": "assistant",
                                                        "done": False}}
                    if run_id:
                        evt["run_id"] = run_id
                    await bus.publish(evt)
            try:
                await asyncio.wait_for(_stream(), timeout=settings.worker_timeout_s)
            except Exception:
                pass   # fall through; empty parts triggers the fallback text below
            text_out = "".join(parts) or "I'm here - go ahead."
            return {"next": "FINISH", "hops": hops + 1, "messages": [AIMessage(
                content=text_out, name="supervisor",
                additional_kwargs={"published": bool(parts)})]}

        await bus.publish(_evt("tool_call", {
            "agent": "supervisor", "tool": "route",
            "args_preview": f"-> {route} ({source})", "status": "end"}, run_id))
        return {"next": route, "hops": hops + 1,
                "scratch": {**state.get("scratch", {}), "task": segment}}

    return supervisor
