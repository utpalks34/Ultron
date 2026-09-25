"""OS worker: the CPU router model decides the intent, registry-backed actions execute it. No GPU.

parse_os_command() is pure and regex-based; it is only a latency fast path for clean, exact
phrasings. Whenever it answers "unknown" (which covers almost every real speech transcript) the
CPU router model (llm_decide_os_action) is the primary decider, with one structured-output try
and the result mapped back onto the same OsAction. run_os_action() executes the action:
names resolve through the registries (never guessed) and every side effect goes through
tools.registry.gate first. The answer is one plain sentence; this node does NOT publish tokens
(os_agent is in SELF_PUBLISHING only so router-model tokens stay out of the chat), so the
orchestrator sends the whole sentence.
"""
import asyncio
import os
import re
import urllib.parse
from pathlib import Path
from typing import Literal, Optional

import psutil
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from config import settings
from tools import desktop, music, registry
from tools import resolve as resolver
from tools.context import ToolContext
from tools.desktop import DesktopError
from tools.music import MusicError

AGENT = "os_agent"
PROMPT = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "os_agent.md"
DB_DOWN = "The database is unavailable, so I can't look that up."


class OsAction(BaseModel):
    kind: Literal["open_app", "close_app", "force_close", "close_all", "play", "pause", "resume",
                  "next", "previous", "volume", "whatsapp", "unsupported", "unknown"]
    target: str = ""
    query: str = ""
    direction: str = ""
    value: int | None = None
    name: str = ""
    message: str = ""


class OsToolCall(BaseModel):
    """Structured output of the LLM decision; mapped onto OsAction by _to_action()."""
    action: Literal["open_app", "close_app", "force_close_app", "close_all",
                    "play_music", "music_control", "set_volume", "whatsapp_compose",
                    "unsupported", "unknown"]
    target: str = ""
    query: str = ""
    control: str = ""
    direction: str = ""
    value: Optional[int] = None
    name: str = ""
    message: str = ""


# --------------------------------------------------------------------------- pure parser

_PRE = r"^\s*(?:please[,\s]+)?"
_TAIL = r"(?:\s+(?:a\s+bit|a\s+little|please|now))*\s*[.!?]*\s*$"
_MEDIA_OBJ = (r"(?:\s+(?:the\s+|my\s+|this\s+|that\s+)?"
              r"(?:music|songs?|tracks?|playback|playing|it))?")
_SONG_OBJ = r"(?:\s+(?:this|the|that|current))?(?:\s+(?:song|track|one))?"

_UNSUPPORTED = re.compile(
    r"\b(?:shut\s?down|restart|reboot|log\s?off|sleep|hibernate)\b"
    r"(?:\s+\w+){0,3}?\s+(?:computer|pc|laptop|system|windows)\b"
    r"|\b(?:shutdown|reboot)\b", re.I)
_WHATSAPP = re.compile(
    _PRE +
    r"(?:send\s+(?:a\s+)?(?:whatsapp(?:\s+message)?|message|text)\s+to|message|text|ping|whatsapp)\s+"
    r"(?P<name>[^\W\d_](?:[^\W\d_]|[ .'’])*?)"
    r"(?:\s+on\s+whatsapp)?"
    r"(?:\s+(?:that|saying|to\s+say)\s+|\s*:\s*)"
    r"(?P<msg>\S.*?)\s*$", re.I | re.S)
_CLOSE_ALL = re.compile(
    _PRE + r"(?:close|quit|shut(?:\s+down)?)\s+"
    r"(?:everything|all(?:\s+my)?(?:\s+(?:apps|windows|programs))?)"
    r"(?:\s+(?:please|now))?\s*[.!?]*\s*$", re.I)
_FORCE = re.compile(_PRE + r"(?:force[\s-]*(?:close|quit)|kill)\s+(?P<t>.+?)\s*$", re.I)
_CLOSE = re.compile(
    _PRE + r"(?:close|quit|exit|shut\s*down|terminate)\s+(?P<t>.+?)\s*$", re.I)
_VOL_SET = re.compile(r"\bvolume\s+(?:to|at)\s+(\d{1,3})\b", re.I)
_VOL_MUTE = re.compile(r"\b(?:un)?mute\b", re.I)
_VOL_UP = re.compile(
    r"\b(?:louder|volume\s+up|turn\s+(?:it\s+|the\s+(?:volume|sound|music)\s+)?up)\b"
    r"|\bincrease(?:\s+(?:the\s+)?(?:volume|sound))?" + _TAIL, re.I)
_VOL_DOWN = re.compile(
    r"\b(?:quieter|volume\s+down|turn\s+(?:it\s+|the\s+(?:volume|sound|music)\s+)?down)\b"
    r"|\blower(?:\s+(?:the\s+)?(?:volume|sound))?" + _TAIL, re.I)
_PAUSE = re.compile(_PRE + r"pause\b" + _MEDIA_OBJ + _TAIL, re.I)
_RESUME = re.compile(_PRE + r"(?:resume|continue|unpause)\b" + _MEDIA_OBJ + _TAIL, re.I)
_NEXT = re.compile(_PRE + r"(?:next|skip)\b" + _SONG_OBJ + _TAIL, re.I)
_PREV = re.compile(_PRE + r"(?:previous|prev|go\s+back)\b" + _SONG_OBJ + _TAIL, re.I)
_PLAY = re.compile(_PRE + r"(?:play|put\s+on)\b\s*(?P<q>.*?)\s*$", re.I)
_OPEN = re.compile(
    _PRE + r"(?:open|launch|start|run|boot\s+up|fire\s+up|bring\s+up)\s+(?P<t>.+?)\s*$", re.I)

_LEAD = re.compile(r"^(?:my|the|up)\s+", re.I)
_TRAIL = re.compile(r"\s+(?:app|application|program|please)$", re.I)


def _clean_target(t: str) -> str:
    t = t.strip()
    prev = None
    while prev != t:
        prev = t
        t = _TRAIL.sub("", _LEAD.sub("", t.rstrip(".!?,;").strip())).strip()
    return t


def parse_os_command(text: str) -> OsAction:
    t = " ".join(text.split())

    if _UNSUPPORTED.search(t):
        return OsAction(kind="unsupported")

    m = _WHATSAPP.match(t)
    if m:
        return OsAction(kind="whatsapp", name=m["name"].strip(), message=m["msg"].strip())

    if _CLOSE_ALL.match(t):
        return OsAction(kind="close_all")

    for pattern, kind in ((_FORCE, "force_close"), (_CLOSE, "close_app")):
        m = pattern.match(t)
        if m and _clean_target(m["t"]):
            return OsAction(kind=kind, target=_clean_target(m["t"]))

    m = _VOL_SET.search(t)
    if m:
        return OsAction(kind="volume", direction="set", value=int(m.group(1)))
    if _VOL_MUTE.search(t):
        return OsAction(kind="volume", direction="mute")
    if _VOL_UP.search(t):
        return OsAction(kind="volume", direction="up")
    if _VOL_DOWN.search(t):
        return OsAction(kind="volume", direction="down")

    for pattern, kind in ((_PAUSE, "pause"), (_RESUME, "resume"), (_NEXT, "next"),
                          (_PREV, "previous")):
        if pattern.match(t):
            return OsAction(kind=kind)

    m = _PLAY.match(t)
    if m:
        return OsAction(kind="play", query=m["q"])

    m = _OPEN.match(t)
    if m and _clean_target(m["t"]):
        return OsAction(kind="open_app", target=_clean_target(m["t"]))

    return OsAction(kind="unknown")


# --------------------------------------------------------------------------- LLM decision

_LLM_KIND = {
    "open_app": "open_app", "close_app": "close_app", "force_close_app": "force_close",
    "close_all": "close_all", "play_music": "play", "whatsapp_compose": "whatsapp",
    "unsupported": "unsupported", "unknown": "unknown",
}


def _usable(a: OsAction) -> bool:
    if a.kind in ("open_app", "close_app", "force_close"):
        return bool(a.target.strip())
    if a.kind == "whatsapp":
        return bool(a.name.strip() and a.message.strip())
    if a.kind == "volume":
        return a.direction in ("up", "down", "mute") or (a.direction == "set" and a.value is not None)
    return True


async def llm_decide_os_action(text: str) -> OsToolCall:
    """One structured-output try on the CPU router. Any failure is 'unknown', never a guess."""
    from llm.cpu import cpu_chat

    prompt = PROMPT.read_text(encoding="utf-8")
    try:
        llm = cpu_chat(150).with_structured_output(OsToolCall)
        result = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content=text)]),
            timeout=settings.router_timeout_s)
    except Exception:
        return OsToolCall(action="unknown")
    return result if isinstance(result, OsToolCall) else OsToolCall(action="unknown")


def _to_action(call: OsToolCall) -> OsAction:
    """Map the LLM's choice onto the OsAction the regex path produces, so every execution
    branch is shared. Anything unusable (empty target, bad control) is 'unknown'."""
    if call.action == "music_control":
        kind = call.control.strip().lower()
        action = OsAction(kind=kind if kind in ("pause", "resume", "next", "previous")
                          else "unknown")
    elif call.action == "set_volume":
        action = OsAction(kind="volume", direction=call.direction.strip().lower(),
                          value=call.value)
    else:
        action = OsAction(kind=_LLM_KIND[call.action], target=_clean_target(call.target),
                          query=call.query.strip(), name=call.name.strip(),
                          message=call.message.strip())
    return action if _usable(action) else OsAction(kind="unknown")


# --------------------------------------------------------------------------- execution

_NEEDS_DB = {"open_app", "close_app", "force_close", "close_all", "play", "whatsapp"}
_TOOL = {
    "open_app": "open_app", "close_app": "close_app", "force_close": "force_close_app",
    "close_all": "close_all", "play": "play_music", "pause": "music_control",
    "resume": "music_control", "next": "music_control", "previous": "music_control",
    "volume": "set_volume", "whatsapp": "whatsapp_compose", "unsupported": "unsupported",
    "unknown": "unknown",
}
_CONTROLS = {   # kind -> (function name in tools.music, answer); looked up at call time
    "pause": ("pause", "Paused."),
    "resume": ("resume", "Resumed."),
    "next": ("next_track", "Skipped to the next track."),
    "previous": ("previous_track", "Back to the previous track."),
}
_TABLE_HINT = {"app": "name, aliases, launch_cmd", "contact": "name, aliases, phone"}


def _preview(a: OsAction) -> str:
    if a.direction:
        return f"{a.direction} {a.value}" if a.value is not None else a.direction
    return a.target or a.query or a.name


def _options(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


async def _find(pool, kind: str, phrase: str) -> tuple[dict | None, str]:
    """(row, "") on one confident match; (None, answer) when ambiguous or missing."""
    decision, rows = await resolver.resolve(pool, kind, phrase)
    if decision == "one":
        return rows[0], ""
    if decision == "ambiguous":
        return None, f"Did you mean {_options([r['name'] for r in rows])}? Say the exact name."
    table = resolver.KINDS[kind][0]
    return None, (f"I don't have '{phrase}' in my registry. Add a row to {table} "
                  f"({_TABLE_HINT[kind]}) and ask again.")


def _ancestor_names() -> set[str]:
    """taskkill works by image name, so an ancestor's image name protects every process of it."""
    names = set()
    for pid in desktop.ancestor_pids():
        try:
            names.add(psutil.Process(pid).name().lower())
        except psutil.Error:
            pass
    return names


async def _open_app(ctx, pool, a: OsAction) -> str:
    row, answer = await _find(pool, "app", a.target)
    if row is None:
        return answer
    if not await registry.gate(ctx, AGENT, "open_app", f"Open {row['name']}"):
        return f"Not approved, so I did not open {row['name']}."
    await desktop.launch(row["launch_cmd"])
    return f"Opened {row['name']}."


async def _close_app(ctx, pool, a: OsAction, force: bool) -> str:
    row, answer = await _find(pool, "app", a.target)
    if row is None:
        return answer
    name, proc = row["name"], row.get("process_name")
    # also the image names of this backend's ancestors: otherwise closing the app that hosts the
    # backend's terminal (e.g. Code.exe inside VS Code's integrated terminal) kills the backend
    if (row.get("protected") or (proc or "").lower() in desktop.PROTECTED_NAMES
            or (proc or "").lower() in _ancestor_names()):
        return f"{name} is protected, so I won't close it."
    if not proc:
        return (f"I don't know {name}'s process name. Set process_name on its row in "
                "registry_apps and ask again.")
    if force:
        ok = await registry.gate(ctx, AGENT, "force_close_app",
                                 f"Force close {name} ({proc}); unsaved work will be lost")
    else:
        ok = await registry.gate(
            ctx, AGENT, "close_app", f"Close {name} ({proc})",
            risk="destructive" if settings.confirm_graceful_close else None)
    if not ok:
        return f"Not approved, so I did not close {name}."
    res = await desktop.close_process(proc, force=force)
    if res["closed"]:
        return f"{'Force closed' if force else 'Closed'} {name}."
    if force:
        return f"{name} is still running even after a forced close."
    return (f"{name} is still running (it may be asking you to save) - "
            f"say 'force close {name}'.")


async def _close_all(ctx, pool) -> str:
    rows = await pool.fetch(
        "SELECT process_name FROM registry_apps WHERE protected AND process_name IS NOT NULL")
    protected = (set(desktop.PROTECTED_NAMES) | {r["process_name"].lower() for r in rows}
                 | _ancestor_names())
    windows = await asyncio.to_thread(desktop.visible_windows)
    targets = desktop.filter_targets(windows, protected, desktop.ancestor_pids())
    if not targets:
        return "Nothing to close."
    names = [t["name"] for t in targets]
    description = f"Close {len(names)} app{'s' if len(names) != 1 else ''}: {', '.join(names)}"
    if not await registry.gate(ctx, AGENT, "close_all", description):
        return "Not approved, so I closed nothing."
    results = await asyncio.gather(*(desktop.close_process(n) for n in names),
                                   return_exceptions=True)
    still = [n for n, r in zip(names, results)
             if isinstance(r, BaseException) or not r["closed"]]
    answer = f"Closed {len(names) - len(still)} of {len(names)} apps."
    if still:
        answer += f" Still running: {', '.join(still)}."
    return answer


async def _play(ctx, pool, a: OsAction) -> str:
    req = music.parse_play("play " + a.query)
    if not await registry.gate(ctx, AGENT, "play_music", f"Play {a.query or 'music'}"):
        return "Not approved, so I did not start playback."
    return await music.play(ctx, pool, req)


async def _control(ctx, a: OsAction) -> str:
    fn_name, answer = _CONTROLS[a.kind]
    if not await registry.gate(ctx, AGENT, "music_control", a.kind):
        return "Not approved, so I left the music alone."
    await asyncio.to_thread(getattr(music, fn_name))
    return answer


async def _volume(ctx, a: OsAction) -> str:
    if a.direction == "set":
        if a.value is None or not 0 <= a.value <= 100:
            return "Tell me a volume between 0 and 100."
    elif a.direction not in ("up", "down", "mute"):
        return "Say volume up, volume down, mute, or a level from 0 to 100."
    if not await registry.gate(ctx, AGENT, "set_volume", _preview(a)):
        return "Not approved, so I left the volume alone."
    if a.direction == "set":
        await asyncio.to_thread(desktop.volume_set, a.value)
        return f"Volume set to {a.value}%."
    await asyncio.to_thread(desktop.volume_step, a.direction)
    return {"up": "Volume up.", "down": "Volume down.", "mute": "Toggled mute."}[a.direction]


async def _whatsapp(ctx, pool, a: OsAction) -> str:
    row, answer = await _find(pool, "contact", a.name)
    if row is None:
        return answer
    digits = re.sub(r"\D", "", row.get("phone") or "")
    if not digits:
        return (f"{row['name']} has no phone number. Set phone on their row in "
                "registry_contacts and ask again.")
    message = a.message.strip()
    if not message:
        return f"What should the message to {row['name']} say?"
    url = "whatsapp://send?phone=" + digits + "&text=" + urllib.parse.quote(message, safe="")
    if not await registry.gate(ctx, AGENT, "whatsapp_compose",
                               f"Open WhatsApp with a message to {row['name']}: {message}"):
        return f"Not approved, so I did not open WhatsApp for {row['name']}."
    try:
        await asyncio.to_thread(os.startfile, url)     # composes only; the user presses Enter
    except OSError as exc:
        raise DesktopError(f"could not open WhatsApp: {exc}") from exc
    return f"Opened WhatsApp with your message to {row['name']}: {message} - press Enter to send."


async def _dispatch(ctx, pool, a: OsAction) -> str:
    if a.kind in _NEEDS_DB and pool is None:
        return DB_DOWN
    if a.kind == "unsupported":
        return "I can't shut down, restart or sleep the computer - that isn't enabled."
    if a.kind == "unknown":
        return "I can't do that yet. Nothing in my registry or command list matches."
    if a.kind == "open_app":
        return await _open_app(ctx, pool, a)
    if a.kind in ("close_app", "force_close"):
        return await _close_app(ctx, pool, a, force=a.kind == "force_close")
    if a.kind == "close_all":
        return await _close_all(ctx, pool)
    if a.kind == "play":
        return await _play(ctx, pool, a)
    if a.kind in _CONTROLS:
        return await _control(ctx, a)
    if a.kind == "volume":
        return await _volume(ctx, a)
    return await _whatsapp(ctx, pool, a)


async def run_os_action(ctx, pool, action: OsAction) -> str:
    tool = _TOOL[action.kind]
    await ctx.emit(tool, "start", _preview(action))
    try:
        answer = await _dispatch(ctx, pool, action)
    except (DesktopError, MusicError) as exc:
        answer = f"Couldn't: {exc}"
    await ctx.emit(tool, "end", answer)
    return answer


def _last_human(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def os_run_worker(name, state, config, bus, ollama, *, pool) -> AIMessage:
    task = (state.get("scratch") or {}).get("task") or _last_human(state)
    ctx = ToolContext(bus, pool, state.get("run_id"), AGENT)
    action = parse_os_command(task)
    if action.kind == "unknown":
        action = _to_action(await llm_decide_os_action(task))
    return AIMessage(content=await run_os_action(ctx, pool, action), name=name)
